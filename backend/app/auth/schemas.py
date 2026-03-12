import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr


class CompleteRegistrationRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)
    tos_accepted: bool


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class MessageResponse(BaseModel):
    message: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    is_provisional: bool
    email_verified: bool
    subscription_status: str
    created_at: datetime

    model_config = {"from_attributes": True}
