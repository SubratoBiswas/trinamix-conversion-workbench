"""Cleansing & validation endpoints — scoped to a Conversion."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.conversion import Conversion
from app.models.user import User
from app.models.validation import ValidationIssue
from app.schemas.runtime import ValidationIssueOut
from app.services.auth_service import get_current_user
from app.services.quality_service import run_cleansing, run_validation

router = APIRouter(prefix="/api/conversions", tags=["quality"])


@router.post("/{conversion_id}/profile-cleansing", response_model=list[ValidationIssueOut])
def profile_cleansing(
    conversion_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    c = db.query(Conversion).filter(Conversion.id == conversion_id).first()
    if not c:
        raise HTTPException(404, "Conversion not found")
    if not c.dataset_id:
        raise HTTPException(400, "Conversion has no source dataset bound")
    return run_cleansing(db, c)


@router.get("/{conversion_id}/cleansing-issues", response_model=list[ValidationIssueOut])
def get_cleansing_issues(
    conversion_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    return (
        db.query(ValidationIssue)
        .filter(
            ValidationIssue.conversion_id == conversion_id,
            ValidationIssue.category == "cleansing",
        )
        .all()
    )


@router.post("/{conversion_id}/validate", response_model=list[ValidationIssueOut])
def validate(
    conversion_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    c = db.query(Conversion).filter(Conversion.id == conversion_id).first()
    if not c:
        raise HTTPException(404, "Conversion not found")
    if not c.template_id:
        raise HTTPException(400, "Conversion has no FBDI target template bound")
    return run_validation(db, c)


@router.get("/{conversion_id}/validation-issues", response_model=list[ValidationIssueOut])
def get_validation_issues(
    conversion_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)
):
    return (
        db.query(ValidationIssue)
        .filter(
            ValidationIssue.conversion_id == conversion_id,
            ValidationIssue.category == "validation",
        )
        .all()
    )
