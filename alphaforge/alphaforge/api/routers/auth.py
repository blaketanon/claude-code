from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from alphaforge.api.auth import authenticate, create_token, current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token")
def token(form: OAuth2PasswordRequestForm = Depends()):
    u = authenticate(form.username, form.password)
    if not u:
        raise HTTPException(401, "bad credentials")
    return {"access_token": create_token(u.username, u.role), "token_type": "bearer", "role": u.role}


@router.get("/me")
def me(user=Depends(current_user)):
    return user
