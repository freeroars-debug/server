import os
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv
from clerk_backend_api import AuthenticateRequestOptions, Clerk
import uvicorn



app = FastAPI()

load_dotenv()

clerk_client = Clerk(bearer_auth=os.getenv('CLERK_SECRET_KEY'))
ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', 'http://localhost:3000').split(',')

if not os.getenv("SUPABASE_API_URL") or not os.getenv("SUPABASE_SERVICE_KEY"):
    raise ValueError("SUPABASE_API_URL and SUPABASE_SERVICE_KEY must be set")


supabase: Client = create_client(
    os.getenv("SUPABASE_API_URL"),
    os.getenv("SUPABASE_SERVICE_KEY"),
)

app = FastAPI(
    title="AI Engineering API",
    description="Backend API for Six-Figure AI Engineering application",
    version="1.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": "Six-Figure AI Engineering app is running!"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": "1.0.0"}

@app.post("/api/users/webhook")
async def create_user_from_clerk_webhook(clerk_webhook_data: dict):
    """
    Handle Clerk user.created webhook event
    
    Logic Flow:
    1. Validate webhook payload structure
    2. Check event type (only process user.created)
    3. Extract and validate clerk_id
    4. Check for duplicate users (webhooks can be retried)
    5. Create new user in database
    6. Return success response
    """
    try:
        # Step 1: Validate webhook payload structure
        if not isinstance(clerk_webhook_data, dict):
            raise HTTPException(
                status_code=400, 
                detail="Invalid webhook payload format"
            )
        
        # Step 2: Check event type
        event_type = clerk_webhook_data.get("type")
        if event_type != "user.created":
            # Return success for other events (don't retry)
            return {
                "success": True,
                "message": f"Event type '{event_type}' ignored"
            }
        
        # Step 3: Extract and validate user data
        user_data = clerk_webhook_data.get("data")
        if not user_data or not isinstance(user_data, dict):
            raise HTTPException(
                status_code=400,
                detail="Missing or invalid user data in webhook payload"
            )
        
        # Step 4: Extract and validate clerk_id
        clerk_id = user_data.get("id")
        if not clerk_id or not isinstance(clerk_id, str):
            raise HTTPException(
                status_code=400, 
                detail="Missing or invalid clerk_id in user data"
            )
        
        # Step 5: Check if user already exists (webhook idempotency)
        existing_user = (
            supabase.table("users")
            .select("clerk_id")
            .eq("clerk_id", clerk_id)
            .execute()
        )
        
        if existing_user.data:
            # User already exists - return success (don't retry webhook)
            return {
                "success": True,
                "message": "User already exists",
                "clerk_id": clerk_id
            }
        
        # Step 6: Create new user in database
        result = supabase.table("users").insert({
            "clerk_id": clerk_id
        }).execute()
        
        # Step 7: Verify insertion was successful
        if not result.data:
            raise HTTPException(
                status_code=500, 
                detail="Failed to create user in database"
            )
        
        return {
            "success": True,
            "message": "User created successfully",
            "user": result.data[0]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        # Only catch unexpected exceptions (database errors, network errors, etc.)
        raise HTTPException(
            status_code=500,
            detail=f"An internal server error occurred while processing webhook: {str(e)}"
        )


async def get_current_user(request: Request) -> str: 
    try:

        request_state = clerk_client.authenticate_request(
            request,
            AuthenticateRequestOptions(
                authorized_parties=["http://localhost:3000"]
            )
        )
        
        if not request_state.is_signed_in:
            raise HTTPException(status_code=401, detail="Not authenticated")
        
        clerk_id = request_state.payload.get("sub")
        print(clerk_id)
        if not clerk_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        return clerk_id
        
    except Exception as e:
        raise HTTPException(
            status_code=401, 
            detail=f"Authentication failed: {str(e)}"
        ) 

@app.get("/api/projects")
def get_projects(clerk_id: str =Depends(get_current_user)):
    try:
        result = supabase.table('projects').select('*').eq('clerk_id', clerk_id).execute()

        return{
            "message": "Projects retrieved successfully",
            "data": result.data
        }

    except Exception as e:
        # Only catch unexpected exceptions (database errors, network errors, etc.)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve the projects: {str(e)}"
        )

"""
`/api/projects`

List all projects: GET `/api/projects`
Create a new project: POST `/api/projects`
Delete a project: DELETE `/api/projects/{project_id}`
Get a project: GET `/api/projects/{project_id}`
Get project chats: GET `/api/projects/{project_id}/chats`
Get project settings: GET `/api/projects/{project_id}/settings`
Update project settings: PUT `/api/projects/{project_id}/settings`
"""

class ProjectCreate(BaseModel):
    name: str
    description: str = ""

class ProjectSettings(BaseModel):
    embedding_model: str
    rag_strategy: str
    agent_type: str
    chunks_per_search: int
    final_context_size: int
    similarity_threshold: float
    number_of_queries: int
    reranking_enabled: bool
    reranking_model: str
    vector_weight: float
    keyword_weight: float

@app.post("/api/projects") 
def create_project(project: ProjectCreate, clerk_id: str = Depends(get_current_user)):
    print(clerk_id)
    """
    Create a new project with default settings
    
    Logic Flow:
    1. Insert new project into database
    2. Create default project settings
    3. If settings creation fails, rollback project creation
    4. Return created project
    """
    try:
        # Step 1: Insert new project into database
        project_result = supabase.table("projects").insert({
            "name": project.name, 
            "description": project.description,
            "clerk_id": clerk_id
        }).execute()

        if not project_result.data:
            raise HTTPException(
                status_code=422, 
                detail="Failed to create project - invalid data provided"
            )

        created_project = project_result.data[0]
        project_id = created_project["id"]

        # Step 2: Create default settings for the project 
        settings_result = supabase.table("project_settings").insert({
            "project_id": project_id, 
            "embedding_model": "text-embedding-3-large",
            "rag_strategy": "basic",
            "agent_type": "agentic",
            "chunks_per_search": 10,
            "final_context_size": 5,
            "similarity_threshold": 0.3,
            "number_of_queries": 5,
            "reranking_enabled": True,
            "reranking_model": "rerank-english-v3.0",
            "vector_weight": 0.7,
            "keyword_weight": 0.3,
        }).execute()

        if not settings_result.data:
            # Step 3: Rollback - Delete the project if settings creation fails
            supabase.table("projects").delete().eq("id", project_id).execute()
            raise HTTPException(
                status_code=422, 
                detail="Failed to create project settings - project creation rolled back"
            )

        return {
            "success": True,
            "message": "Project created successfully", 
            "data": created_project 
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"An internal server error occurred while creating project: {str(e)}"
        )


@app.delete("/api/projects/{project_id}")
def delete_project(
    project_id: str, 
    clerk_id: str = Depends(get_current_user)
):
    """
    Delete a project and all related data
    
    Logic Flow:
    1. Verify project exists and belongs to user
    2. Delete project (CASCADE handles all related data: settings, documents, chunks, chats, messages)
    """
    try:
        # Step 1: Verify project exists and belongs to user 
        project_result = supabase.table("projects").select("*").eq("id", project_id).eq("clerk_id", clerk_id).execute()

        if not project_result.data: 
            raise HTTPException(
                status_code=404, 
                detail="Project not found or you don't have permission to delete it"
            )

        # Step 2: Delete project (CASCADE handles all related data)
        deleted_result = supabase.table("projects").delete().eq("id", project_id).eq("clerk_id", clerk_id).execute()

        if not deleted_result.data: 
            raise HTTPException(
                status_code=404, 
                detail="Failed to delete project - project not found"
            )

        return {
            "success": True,
            "message": "Project deleted successfully", 
            "data": deleted_result.data[0]  
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"An internal server error occurred while deleting project: {str(e)}"
        )


@app.get("/api/projects/{project_id}")
async def get_project(
    project_id: str, 
    clerk_id: str = Depends(get_current_user)
):
    """
    Retrieve a specific project by ID
    """
    try:
        result = supabase.table("projects").select("*").eq("id", project_id).eq("clerk_id", clerk_id).execute()

        if not result.data:
            raise HTTPException(
                status_code=404, 
                detail="Project not found or you don't have permission to access it"
            )

        return {
            "success": True,
            "message": "Project retrieved successfully", 
            "data": result.data[0]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"An internal server error occurred while retrieving project: {str(e)}"
        )


@app.get("/api/projects/{project_id}/chats")
async def get_project_chats(
    project_id: str, 
    clerk_id: str = Depends(get_current_user)
):
    """
    Retrieve all chats for a specific project
    """
    try:
        result = supabase.table("chats").select("*").eq("project_id", project_id).eq("clerk_id", clerk_id).order("created_at", desc=True).execute()

        return {
            "success": True,
            "message": "Project chats retrieved successfully", 
            "data": result.data or []
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"An internal server error occurred while retrieving project chats: {str(e)}"
        )

@app.get("/api/projects/{project_id}/settings")
async def get_project_settings(
    project_id: str, 
    clerk_id: str = Depends(get_current_user)
):
    """
    Retrieve settings for a specific project
    """
    try:
        settings_result = supabase.table("project_settings").select("*").eq("project_id", project_id).execute()

        if not settings_result.data:
            raise HTTPException(
                status_code=404, 
                detail="Project settings not found"
            )

        return {
            "success": True,
            "message": "Project settings retrieved successfully", 
            "data": settings_result.data[0]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"An internal server error occurred while retrieving project settings: {str(e)}"
        )

@app.get("/api/projects/{project_id}/files")
async def get_project_files(
    project_id: str, 
    clerk_id: str = Depends(get_current_user)
):
    try:
        # Get all files for this project - FK constraints ensure project exists and belongs to the user
        result = supabase.table("project_documents").select("*").eq("project_id", project_id).eq("clerk_id", clerk_id).order("created_at", desc=True).execute()

        return {
            "message": "Project files retrieved successfully", 
            "data": result.data or []
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail = f"Failed to get project files: {str(e)}")

class ChatCreate(BaseModel):
    title: str
    project_id: str


@app.post("/api/chats")
async def create_chat(
    chat: ChatCreate, 
    clerk_id: str = Depends(get_current_user)
):
    try:
        result = supabase.table("chats").insert({
            "title": chat.title, 
            "project_id": chat.project_id, 
            "clerk_id": clerk_id
        }).execute()

        return {
            "message": "Chat created successfully", 
            "data": result.data[0]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail = f"Failed to create chat: {str(e)}")


@app.delete("/api/chats/{chat_id}")
async def delete_chat(
    chat_id: str, 
    clerk_id: str = Depends(get_current_user)
):
    try:
        deleted_result = supabase.table("chats").delete().eq("id", chat_id).eq("clerk_id", clerk_id).execute()

        if not deleted_result.data: 
            raise HTTPException(status_code=404, detail="Chat not found or access denied")

        return {
            "message": "Chat Deleted Successfully", 
            "data": deleted_result.data[0]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail = f"Failed to delete chat: chat_id")

@app.get("/api/projects/{project_id}/settings")
async def get_project_settings(
    project_id: str, 
    clerk_id: str = Depends(get_current_user)
):

    try: 
       
        settings_result = supabase.table("project_settings").select("*").eq("project_id", project_id).execute()

        if not settings_result.data:
            raise HTTPException(status_code=404, detail="Project settings not found")

        return {
            "message": "Project settings retrieved successfully", 
            "data": settings_result.data[0]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail = f"Failed to get project settings: {str(e)}")


@app.put("/api/projects/{project_id}/settings")
async def update_project_settings(
    project_id: str, 
    settings: ProjectSettings, 
    clerk_id: str = Depends(get_current_user)
): 
    try: 
        # First verify the project exists and belongs to the user
        project_result = supabase.table("projects").select("id").eq("id", project_id).eq("clerk_id", clerk_id).execute()    

        if not project_result.data:
            raise HTTPException(status_code=404, detail = f"Project not found or access denied")

        # Perform the update
        result = supabase.table("project_settings").update(settings.model_dump()).eq("project_id", project_id).execute()

        if not result.data:
            raise HTTPException(status_code=404, detail = f"Project settings not found")

        return {
            "message": "Project settings updated successfully", 
            "data": result.data[0]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail = f"Failed to update project settings: {str(e)}")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)