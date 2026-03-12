import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user_allow_provisional
from app.db.models import Manuscript, StoryBible, User
from app.db.session import get_db

router = APIRouter(prefix="/bible", tags=["story-bible"])


@router.get("/{manuscript_id}")
async def get_story_bible(
    manuscript_id: uuid.UUID,
    user: User = Depends(get_current_user_allow_provisional),
    db: AsyncSession = Depends(get_db),
):
    """Get the story bible for a manuscript. Scoped to current user.
    Provisional users can access this (free bible preview).
    """
    # Verify manuscript ownership
    ms_result = await db.execute(
        select(Manuscript).where(
            Manuscript.id == manuscript_id,
            Manuscript.user_id == user.id,
            Manuscript.deleted_at.is_(None),
        )
    )
    manuscript = ms_result.scalar_one_or_none()
    if manuscript is None:
        raise HTTPException(status_code=404, detail="Manuscript not found")

    bible_result = await db.execute(
        select(StoryBible).where(StoryBible.manuscript_id == manuscript_id)
    )
    bible = bible_result.scalar_one_or_none()
    if bible is None:
        raise HTTPException(status_code=404, detail="Story bible not yet generated")

    return {
        "manuscript_id": str(manuscript_id),
        "version": bible.version,
        "bible": bible.bible_json,
        "updated_at": bible.updated_at.isoformat(),
    }
