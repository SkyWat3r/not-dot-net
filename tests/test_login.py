from nicegui import ui
from nicegui.testing import User

async def test_click(user: User) -> None:
    await user.open('/login')
    await user.should_see('Click me')