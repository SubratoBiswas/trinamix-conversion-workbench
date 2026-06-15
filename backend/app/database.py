"""MongoDB connection and Beanie ODM initialisation."""
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.MONGODB_URL)
    return _client


async def init_db() -> None:
    from beanie import init_beanie
    from app.models.user import User
    from app.models.project import Project
    from app.models.conversion import Conversion
    from app.models.dataset import Dataset, DatasetColumnProfile
    from app.models.fbdi import FbdiTemplate, FbdiTargetObject
    from app.models.mapping import MappingRecord
    from app.models.learned import LearnedPattern
    from app.models.quality import QualityCheck
    from app.models.workflow import Workflow
    from app.models.load import LoadRun, LoadError
    from app.models.v10 import (
        SourceConnection, DiscoveryRun, DiscoveredObject,
        AuditEvent, CoaStructure, CoaSegment, CoaValueCrosswalk,
        Issue, Risk, SignOff, DressRehearsal, CutoverTask,
        ReconciliationCheck,
    )

    client = get_client()
    await init_beanie(
        database=client[settings.MONGODB_DB],
        document_models=[
            User, Project, Conversion, Dataset, DatasetColumnProfile,
            FbdiTemplate, FbdiTargetObject,
            MappingRecord, LearnedPattern, QualityCheck,
            Workflow, LoadRun, LoadError,
            # v10
            SourceConnection, DiscoveryRun, DiscoveredObject,
            AuditEvent, CoaStructure, CoaSegment, CoaValueCrosswalk,
            Issue, Risk, SignOff, DressRehearsal, CutoverTask,
            ReconciliationCheck,
        ],
    )
