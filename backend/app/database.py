"""MongoDB database setup using Motor + Beanie."""
import motor.motor_asyncio
from beanie import init_beanie

from app.config import settings


_client: motor.motor_asyncio.AsyncIOMotorClient | None = None


def get_client() -> motor.motor_asyncio.AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = motor.motor_asyncio.AsyncIOMotorClient(settings.MONGODB_URI)
    return _client


async def init_db() -> None:
    """Initialise Beanie with all document models. Called on app startup."""
    from app.models.user import User
    from app.models.project import Project
    from app.models.conversion import Conversion
    from app.models.dataset import Dataset, DatasetColumnProfile
    from app.models.fbdi import FBDITemplate, FBDISheet, FBDIField
    from app.models.mapping import MappingSuggestion
    from app.models.transformation import TransformationRule, Crosswalk
    from app.models.learned import LearnedMapping
    from app.models.output import ConvertedOutput
    from app.models.load import LoadRun, LoadError
    from app.models.validation import ValidationIssue
    from app.models.environment import Environment, EnvironmentRun
    from app.models.dependency import Dependency
    from app.models.workflow import Workflow

    client = get_client()
    await init_beanie(
        database=client[settings.MONGODB_DB],
        document_models=[
            User, Project, Conversion, Dataset, DatasetColumnProfile,
            FBDITemplate, FBDISheet, FBDIField, MappingSuggestion,
            TransformationRule, Crosswalk, LearnedMapping,
            ConvertedOutput, LoadRun, LoadError, ValidationIssue,
            Environment, EnvironmentRun, Dependency, Workflow,
        ],
    )
