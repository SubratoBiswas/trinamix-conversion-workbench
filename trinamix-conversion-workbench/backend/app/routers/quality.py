"""Cleansing & validation endpoints."""
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException

from app.models.conversion import Conversion
from app.models.user import User
from app.models.validation import ValidationIssue
from app.schemas.runtime import ValidationIssueOut
from app.services.auth_service import get_current_user
from app.services.quality_service import run_cleansing, run_validation

router = APIRouter(prefix="/api/conversions", tags=["quality"])


def _issue_out(i: ValidationIssue) -> dict:
    d = i.model_dump()
    d["id"] = str(i.id)
    d["conversion_id"] = str(i.conversion_id)
    return d


@router.post("/{conversion_id}/profile-cleansing", response_model=list[ValidationIssueOut])
async def profile_cleansing(conversion_id: str, _: User = Depends(get_current_user)):
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    if not c.dataset_id:
        raise HTTPException(400, "Conversion has no source dataset bound")
    issues = await run_cleansing(c)
    return [_issue_out(i) for i in issues]


@router.get("/{conversion_id}/cleansing-issues", response_model=list[ValidationIssueOut])
async def get_cleansing_issues(conversion_id: str, _: User = Depends(get_current_user)):
    issues = await ValidationIssue.find({
        "conversion_id": PydanticObjectId(conversion_id), "category": "cleansing"
    }).to_list()
    return [_issue_out(i) for i in issues]


@router.post("/{conversion_id}/validate", response_model=list[ValidationIssueOut])
async def validate(conversion_id: str, _: User = Depends(get_current_user)):
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    if not c.template_id:
        raise HTTPException(400, "Conversion has no FBDI target template bound")
    issues = await run_validation(c)
    return [_issue_out(i) for i in issues]


@router.get("/{conversion_id}/validation-issues", response_model=list[ValidationIssueOut])
async def get_validation_issues(conversion_id: str, _: User = Depends(get_current_user)):
    issues = await ValidationIssue.find({
        "conversion_id": PydanticObjectId(conversion_id), "category": "validation"
    }).to_list()
    return [_issue_out(i) for i in issues]
