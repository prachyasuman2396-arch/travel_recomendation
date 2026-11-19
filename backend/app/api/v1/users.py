from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.errors import InvalidRequestError, NotFoundError
from app.database.session import get_db
from app.repositories.admin_repository import UserRepository
from app.schemas.admin import UserCreate, UserOut
from app.schemas.preference import SavedPreferences

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: Session = Depends(get_db)):
    repo = UserRepository(db)
    if body.email and repo.email_taken(body.email):
        raise InvalidRequestError("A user with this email already exists")
    return UserOut.model_validate(repo.create(body.name, body.email, body.preferences.model_dump()))


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: str, db: Session = Depends(get_db)):
    user = UserRepository(db).get(user_id)
    if user is None:
        raise NotFoundError("User not found")
    return UserOut.model_validate(user)


@router.put("/{user_id}/preferences", response_model=UserOut)
def update_preferences(user_id: str, body: SavedPreferences, db: Session = Depends(get_db)):
    repo = UserRepository(db)
    user = repo.get(user_id)
    if user is None:
        raise NotFoundError("User not found")
    return UserOut.model_validate(repo.update_preferences(user, body.model_dump()))
