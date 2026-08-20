"""Reset the admin password of a running Open WebUI instance.

Usage (inside the container, interactive — password is hidden, never hits
shell history or chat):

    docker exec -it pond-open-webui python /tmp/reset_admin_password.py

Or non-interactively (visible in process list — avoid if possible):

    docker exec -it pond-open-webui python /tmp/reset_admin_password.py --password 'newpass'

What it does (verified against this fork's code):
  1. Finds the admin user  (Users.get_user_by_role('admin'))
  2. Reads a new password via getpass (or --password)
  3. Hashes it with open_webui.utils.auth.get_password_hash  (async, argon2/bcrypt)
  4. Auths.update_user_password_by_id(user.id, hashed)  (takes the HASH — not plain)
  5. Re-activates the credential row (auth.active = True) in case it was soft-disabled
  6. Re-verifies with verify_password before reporting success
"""

import argparse
import asyncio
import getpass
import os
import sys


async def main() -> int:
    parser = argparse.ArgumentParser(description='Reset Open WebUI admin password')
    parser.add_argument('--password', help='new password (omitted = hidden prompt)')
    parser.add_argument('--email', help='target a specific account email instead of the admin')
    parser.add_argument('--new-email', help='also change the account email to this value')
    parser.add_argument('--list', action='store_true', help='list all accounts, then exit')
    args = parser.parse_args()

    from open_webui.internal.db import get_async_db_context
    from open_webui.models.auths import Auth, Auths
    from open_webui.models.users import User, UserModel, Users
    from open_webui.utils.auth import get_password_hash, verify_password

    # --- list mode -----------------------------------------------------------
    if args.list:
        async with get_async_db_context() as session:
            from sqlalchemy import select

            rows = (await session.execute(select(User.email, User.role).order_by(User.created_at))).all()
        print('Accounts in DB:')
        for email, role in rows:
            print(f'  - {email}  (role={role})')
        return 0

    # --- locate the account -------------------------------------------------
    if args.email:
        user = await Users.get_user_by_email(args.email.lower())
        if user is None:
            print(f'ERROR: no user with email {args.email!r}')
            return 1
    else:
        user = await Users.get_super_admin_user()
        if user is None:
            # fallback: oldest account in the system (admin is always first)
            async with get_async_db_context() as session:
                from sqlalchemy import select

                row = (
                    await session.execute(select(User).order_by(User.created_at).limit(1))
                ).scalars().first()
                user = UserModel.model_validate(row) if row else None
        if user is None:
            print('ERROR: no admin user found')
            return 1

    print(f'Resetting password for: {user.email} (role={user.role}, id={user.id})')

    # --- read the new password ----------------------------------------------
    if args.password:
        new_password = args.password
    else:
        env_pw = os.environ.get('RESET_PASSWORD', '').strip()
        if env_pw:
            new_password = env_pw
        elif not sys.stdin.isatty():
            # non-interactive caller (AI agent / CI): generate a strong one-time
            # password and print it ONCE for the operator to read off this terminal.
            import secrets

            new_password = secrets.token_urlsafe(9)
            print(f'NON-INTERACTIVE MODE: generated password -> {new_password}')
            print('(shown only here; change it in Settings after first login)')
        else:
            new_password = getpass.getpass('New password: ')
            confirm = getpass.getpass('Confirm      : ')
            if new_password != confirm:
                print('ERROR: passwords do not match')
                return 1

    if len(new_password) < 8:
        print('ERROR: password must be at least 8 characters')
        return 1

    # --- hash + persist (same code path as the API uses) ---------------------
    hashed = await get_password_hash(new_password)
    ok = await Auths.update_user_password_by_id(user.id, hashed)
    if not ok:
        print('ERROR: update_user_password_by_id returned False (auth row missing?)')
        return 1

    # --- re-activate the credential row in case it was soft-disabled ---------
    async with get_async_db_context() as session:
        credential = await session.get(Auth, user.id)
        if credential is None:
            print('ERROR: auth row disappeared mid-reset')
            return 1
        credential.active = True
        await session.commit()

    # --- verify against what is actually stored ------------------------------
    async with get_async_db_context() as session:
        stored = (await session.get(Auth, user.id)).password
    if not await verify_password(new_password, stored):
        print('ERROR: verification after reset FAILED — password was not stored correctly')
        return 1

    # --- optionally change the email too ------------------------------------
    if args.new_email:
        target = args.new_email.strip().lower()
        if not target or '@' not in target:
            print("ERROR: --new-email value doesn't look like an email")
            return 1
        existing = await Users.get_user_by_email(target)
        if existing is not None and existing.id != user.id:
            print(f'ERROR: {target} is already used by another account')
            return 1
        ok = await Auths.update_email_by_id(user.id, target)
        if not ok:
            print('ERROR: update_email_by_id returned False')
            return 1
        print(f'Email updated: {user.email} -> {target}')

    final_email = args.new_email.strip().lower() if args.new_email else user.email
    print('OK: password reset and verified. You can now sign in with:')
    print(f'     email    : {final_email}')
    print(f'     password: (the one you just typed)')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
