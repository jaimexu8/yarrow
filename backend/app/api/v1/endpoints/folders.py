"""User-created folders for organizing owned documents (US-103)."""

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yarrow_db.models import Folder, User

from app.core.database import get_db
from app.core.security import get_current_user
from app.schemas import FolderCreate, FolderOut, FolderRename

router = APIRouter()


async def _owned_folder(db: AsyncSession, folder_id: UUID, owner_id: UUID) -> Folder:
    folder = await db.get(Folder, folder_id)
    if folder is None or folder.owner_id != owner_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Folder not found",
        )
    return folder


async def _name_taken(
    db: AsyncSession, owner_id: UUID, name: str, exclude_id: UUID | None = None
) -> bool:
    query = select(Folder.id).where(
        Folder.owner_id == owner_id,
        func.lower(Folder.name) == name.lower(),
    )
    if exclude_id is not None:
        query = query.where(Folder.id != exclude_id)
    return (await db.execute(query)).scalar_one_or_none() is not None


@router.get("/", response_model=list[FolderOut])
async def list_folders(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the caller's folders, by name."""
    result = await db.execute(
        select(Folder)
        .where(Folder.owner_id == current_user.id)
        .order_by(func.lower(Folder.name))
    )
    return list(result.scalars().all())


@router.post("/", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(
    body: FolderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a folder. Duplicate names for this user are a 409."""
    if await _name_taken(db, current_user.id, body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A folder with that name already exists",
        )
    folder = Folder(id=uuid4(), owner_id=current_user.id, name=body.name)
    db.add(folder)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A folder with that name already exists",
        ) from None
    await db.refresh(folder)
    return folder


@router.patch("/{folder_id}", response_model=FolderOut)
async def rename_folder(
    folder_id: UUID,
    body: FolderRename,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    folder = await _owned_folder(db, folder_id, current_user.id)
    if await _name_taken(db, current_user.id, body.name, exclude_id=folder.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A folder with that name already exists",
        )
    folder.name = body.name
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A folder with that name already exists",
        ) from None
    await db.refresh(folder)
    return folder


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete the folder. Documents in it become unfiled; they are not deleted."""
    folder = await _owned_folder(db, folder_id, current_user.id)
    await db.execute(delete(Folder).where(Folder.id == folder.id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
