"""login/logout: set or clear the password-gate cookie."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.security import COOKIE, token_for

router = APIRouter(prefix="/api", tags=["auth"])


class LoginBody(BaseModel):
    password: str


@router.post("/login")
def login(request: Request, body: LoginBody, response: Response):
    password = request.app.state.settings.sage_password
    if password and not secrets.compare_digest(body.password, password):
        raise HTTPException(status_code=401, detail="wrong password")
    response.set_cookie(COOKIE, token_for(password), httponly=True, samesite="lax", max_age=31536000, path="/")
    return {"ok": True}


@router.get("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}