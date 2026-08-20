"""Integration test for the admin password reset flow (mirrors scripts/reset_admin_password.py)."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))


async def main() -> None:
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.auths import Auth, Auths
    from open_webui.models.users import User, Users
    from open_webui.utils.auth import get_password_hash, verify_password

    # 1. seed an admin + credential row exactly like signup does
    async with get_async_db_context() as session:
        session.add(User(id='test-admin', email='admin@test.local', name='Admin', role='admin', created_at=1234, last_active_at=1234, updated_at=1234))
        session.add(Auth(id='test-admin', email='admin@test.local', password='x', active=True))
        await session.commit()

    # 2. the reset flow
    user = await Users.get_super_admin_user()
    assert user is not None and user.email == 'admin@test.local', f'unexpected admin: {user}'
    print('found admin:', user.email)

    hashed = await get_password_hash('correct horse battery')
    ok = await Auths.update_user_password_by_id(user.id, hashed)
    assert ok, 'update_user_password_by_id returned False'

    # 3. verify what is actually stored
    async with get_async_db_context() as session:
        stored = (await session.get(Auth, user.id)).password
    assert stored != 'x', 'password not updated'
    assert not stored.startswith('<'), 'stored a non-hash value'
    right = await verify_password('correct horse battery', stored)
    wrong = await verify_password('wrong-password', stored)
    assert right, 'verify_password failed for the correct password'
    assert not wrong, 'verify_password passed for a wrong password'
    print('PASS: reset flow works — hash stored, correct password verifies, wrong rejected')


if __name__ == '__main__':
    asyncio.run(main())
