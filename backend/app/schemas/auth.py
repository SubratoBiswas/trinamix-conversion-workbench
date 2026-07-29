"""Auth request/response schemas."""
from pydantic import BaseModel, EmailStr

from app.schemas.oid import ApiOut


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(ApiOut):
    id: str
    name: str
    email: EmailStr
    role: str

    class Config:
        from_attributes = True
        populate_by_name = True

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        if hasattr(obj, 'id') and obj.id is not None:
            data = {
                'id': str(obj.id),
                'name': obj.name,
                'email': obj.email,
                'role': obj.role,
            }
            return cls(**data)
        return super().model_validate(obj, *args, **kwargs)


TokenResponse.model_rebuild()
