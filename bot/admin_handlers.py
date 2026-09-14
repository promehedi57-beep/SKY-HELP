"""
Admin handlers: /preview (Phase 4) + dynamic key management (Phase 5).

Key management requires OWNER or SUPER_ADMIN, verified server-side from the
`admins` table (env ADMIN_ID is treated as OWNER).
"""

import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.formatter import build_context, render, validate_template

logger = logging.getLogger(__name__)

OWNER_ENV_ROLES = ("OWNER", "SUPER_ADMIN")


# ---------- permission helpers ----------

async def get_role(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str | None:
    db = context.bot_data.get("db")
    row = await db.fetchone("SELECT role, enabled FROM admins WHERE user_id = ?", (user_id,))
    if row and row["enabled"]:
        return row["role"]
    if user_id in context.bot_data["settings"].admin_ids:
        return "OWNER"
    return None


async def require_role(context: ContextTypes.DEFAULT_TYPE, user_id: int,
                       roles: tuple[str, ...]) -> bool:
    role = await get_role(context, user_id)
    return role in roles


# ---------- /preview (Phase 4) ----------

PREVIEW_USAGE = (
    "<b>Template preview</b>\n\n"
    "Usage: <code>/preview &lt;template&gt;</code>\n\n"
    "Placeholders: {user} {user_id} {username} {chat} {trigger} {category} "
    "{method} {confidence} {timestamp}"
)


async def cmd_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not await require_role(
            context, user.id, ("OWNER", "SUPER_ADMIN", "ADMIN", "TRAINER")):
        await update.effective_message.reply_text("⛔ Admins only.")
        return
    parts = (update.effective_message.text or "").split(" ", 1)
    template = parts[1] if len(parts) > 1 else ""
    if not template.strip():
        await update.effective_message.reply_text(PREVIEW_USAGE, parse_mode=ParseMode.HTML)
        return
    try:
        warnings = validate_template(template)
        rendered = render(template, build_context(
            user=user, chat=update.effective_chat, trigger="whatsapp",
            category="troubleshooting", method="WhatsApp Troubleshooting",
            confidence=0.95))
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Invalid template: {html.escape(str(e))}")
        return
    out = "✅ <b>Rendered preview:</b>\n\n" + rendered
    if warnings:
        out += "\n\n⚠️ <b>Warnings:</b>\n• " + "\n• ".join(html.escape(w) for w in warnings)
    await update.effective_message.reply_text(out, parse_mode=ParseMode.HTML)


# ---------- key management (Phase 5) ----------

def _fmt_stats(stats: dict) -> str:
    t = stats["totals"]
    lines = [
        "<b>🔑 Gemini API Key Dashboard</b>\n",
        f"Total keys: {stats['total']} | Active: {stats['active']} | "
        f"Cooling: {stats['cooldown']} | Disabled: {stats['disabled']}",
        f"Rotation position: {stats['rotation_position'] + 1}",
        f"Requests: {t['requests']} | Success: {t['success']} | "
        f"429: {t['rate_limited']} | Timeouts: {t['timeouts']} | Failures: {t['failures']}\n",
    ]
    for r in stats["rows"]:
        status = r["status"]
        line = (f"<b>KEY #{r['key_index'] + 1}</b> <code>{html.escape(r['key_prefix'])}</code>\n"
                f"Status: {status}")
        if r["status"] == "COOLDOWN" and r["cooldown_until"]:
            line += f" (until {html.escape(r['cooldown_until'])} UTC)"
        line += (f"\nRequests: {r['total_requests']} | Success: {r['successful_requests']} | "
                 f"429: {r['rate_limit_errors']} | Timeouts: {r['timeout_errors']} | "
                 f"Failures: {r['failed_requests']}\n")
        lines.append(line)
    return "\n".join(lines)


async def cmd_apistats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not await require_role(context, user.id, OWNER_ENV_ROLES):
        await update.effective_message.reply_text("⛔ OWNER/SUPER_ADMIN only.")
        return
    mgr = context.bot_data.get("key_manager")
    if mgr is None:
        await update.effective_message.reply_text("⚠️ Key manager not initialized.")
        return
    stats = await mgr.stats()
    if stats["total"] == 0:
        await update.effective_message.reply_text(
            "No API keys configured. Use /addkey <key> or set GEMINI_API_KEYS in .env.")
        return
    await update.effective_message.reply_text(_fmt_stats(stats), parse_mode=ParseMode.HTML)


async def cmd_addkey(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not await require_role(context, user.id, OWNER_ENV_ROLES):
        await update.effective_message.reply_text("⛔ OWNER/SUPER_ADMIN only.")
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: <code>/addkey YOUR_GEMINI_API_KEY</code>\n"
            "⚠️ Delete this command message afterwards so the key doesn't stay in chat history.",
            parse_mode=ParseMode.HTML)
        return
    mgr = context.bot_data["key_manager"]
    ok, msg = await mgr.add_key(" ".join(context.args))
    emoji = "✅" if ok else "❌"
    await update.effective_message.reply_text(f"{emoji} {html.escape(msg)}")
    # Best effort: delete the command message containing the plaintext key
    try:
        await update.effective_message.delete()
    except Exception:
        pass


async def cmd_delkey(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not await require_role(context, user.id, OWNER_ENV_ROLES):
        await update.effective_message.reply_text("⛔ OWNER/SUPER_ADMIN only.")
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: <code>/delkey AIzaSy...X9a</code> (masked or hash from /apistats)",
            parse_mode=ParseMode.HTML)
        return
    mgr = context.bot_data["key_manager"]
    ok, msg = await mgr.del_key(" ".join(context.args))
    await update.effective_message.reply_text(
        f"{'✅' if ok else '❌'} {html.escape(msg)}")


async def cmd_testkeys(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or not await require_role(context, user.id, OWNER_ENV_ROLES):
        await update.effective_message.reply_text("⛔ OWNER/SUPER_ADMIN only.")
        return
    mgr = context.bot_data.get("key_manager")
    if mgr is None or not mgr._keys:
        await update.effective_message.reply_text("No keys configured to test.")
        return
    msg = await update.effective_message.reply_text("🔎 Testing keys…")
    results = await mgr.test_all_keys()
    lines = ["<b>Key health check</b>\n"]
    for r in results:
        status = "✅ OK" if r["ok"] else f"❌ {r['error']}"
        lines.append(f"<code>{html.escape(r['key'])}</code> — {status} ({r['ms']}ms)")
    await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)
