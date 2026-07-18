"""MongoDB connection and Beanie ODM initialisation."""
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.MONGODB_URI)
    return _client


async def init_db() -> None:
    from beanie import init_beanie
    from app.models.user import User
    from app.models.client import Client
    from app.models.project import Project
    from app.models.conversion import Conversion
    from app.models.dataset import Dataset, DatasetColumnProfile
    from app.models.fbdi import (
        FBDITemplate, FBDISheet, FBDIField, FBDITemplateFile, OracleLookup,
        GoldStandard,
    )
    from app.models.mapping import MappingSuggestion
    from app.models.learned import LearnedMapping
    from app.models.transformation import TransformationRule, Crosswalk
    from app.models.output import ConvertedOutput
    from app.models.workflow import Workflow
    from app.models.load import LoadRun, LoadError
    from app.models.validation import ValidationIssue
    from app.models.dependency import Dependency
    from app.models.environment import Environment, EnvironmentRun
    from app.models.app_setting import AppSetting
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
            User, Project, Conversion,
            Dataset, DatasetColumnProfile,
            FBDITemplate, FBDISheet, FBDIField, FBDITemplateFile, OracleLookup,
            GoldStandard,
            MappingSuggestion, LearnedMapping,
            TransformationRule, Crosswalk,
            ConvertedOutput, Workflow,
            LoadRun, LoadError,
            ValidationIssue, Dependency,
            Environment, EnvironmentRun,
            # v10
            SourceConnection, DiscoveryRun, DiscoveredObject,
            AuditEvent, CoaStructure, CoaSegment, CoaValueCrosswalk,
            Issue, Risk, SignOff, DressRehearsal, CutoverTask,
            ReconciliationCheck,
            AppSetting,
        ],
    )
