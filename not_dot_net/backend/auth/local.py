from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from not_dot_net.backend.schemas import TokenResponse, UserCreate
from not_dot_net.backend.users import get_user_manager, get_jwt_strategy

router = APIRouter(tags=["auth"])


class AuthRequest(BaseModel):
    email: str
    password: str


@router.post("/auth/local", response_model=TokenResponse)
async def local_login(
    credentials: AuthRequest,
    user_manager=Depends(get_user_manager),
):
    form = OAuth2PasswordRequestForm(
        username=credentials.email, password=credentials.password,
        scope="", grant_type="password",
    )
    user = await user_manager.authenticate(form)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    return TokenResponse(access_token=token)


@router.post("/auth/register", response_model=TokenResponse)
async def local_register(
    credentials: AuthRequest,
    user_manager=Depends(get_user_manager),
):
    user_create = UserCreate(email=credentials.email, password=credentials.password)
    try:
        user = await user_manager.create(user_create)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    return TokenResponse(access_token=token)
