"""CKR screen states, detection priority (spec §4) and tap plans (spec §5).

Priority is specific/popup screens BEFORE general ones so look-alikes never get
confused (INVARIANT #5): START_1↔START_3 (Double Coins banner), END_BOX↔
END_BOX_OPEN (Open-all vs Confirm), START_2↔MONEY_POPUP (Multi-Buy vs Cancel).
"""

from __future__ import annotations

from enum import Enum

from ckrbot.engine.screen import ScreenSpec


class State(str, Enum):
    MAIN_MENU = "MAIN_MENU"
    START_1 = "START_1"
    START_2 = "START_2"
    START_3 = "START_3"
    GAMEPLAY = "GAMEPLAY"
    CAPTCHA = "CAPTCHA"
    END_ROUND = "END_ROUND"
    END_BOX = "END_BOX"
    END_BOX_OPEN = "END_BOX_OPEN"
    LEVEL_UP = "LEVEL_UP"
    MENU_REWARD = "MENU_REWARD"
    MONEY_POPUP = "MONEY_POPUP"
    CONN_LOST = "CONN_LOST"
    FRIEND_INFO = "FRIEND_INFO"
    DAILY_CHECKIN = "DAILY_CHECKIN"
    TITLE = "TITLE"
    UNKNOWN = "UNKNOWN"


# Ordered by detection priority (spec §4): index 0 checked first.
CKR_SCREENS: list[ScreenSpec] = [
    # Game relaunch title/loading screen (e.g. after a dropped connection the game
    # re-downloads) — wait it out / tap to start; must NOT be treated as UNKNOWN.
    ScreenSpec(State.TITLE, markers=("tpl_title",)),
    # Blocking network-error overlay — can appear over any screen; tap Confirm to retry.
    ScreenSpec(State.CONN_LOST, markers=("tpl_conn_lost",)),
    # Friend's Info popup (overlay) — close it with the top-right X.
    ScreenSpec(State.FRIEND_INFO, markers=("tpl_friend_info",)),
    # Daily Check-in popup (appears over MAIN_MENU on login) — dismiss with OK.
    ScreenSpec(State.DAILY_CHECKIN, markers=("tpl_daily_checkin",)),
    ScreenSpec(State.MONEY_POPUP, markers=("tpl_cancel",)),
    ScreenSpec(State.START_2, markers=("tpl_multibuy",)),
    ScreenSpec(State.CAPTCHA, markers=("tpl_captcha_header",)),
    ScreenSpec(State.MENU_REWARD, markers=("tpl_congrats_confirm",)),
    ScreenSpec(State.LEVEL_UP, markers=("tpl_levelup_confirm",)),
    ScreenSpec(State.END_BOX_OPEN, markers=("tpl_box_confirm",)),
    ScreenSpec(State.END_BOX, markers=("tpl_open_all",)),
    ScreenSpec(State.END_ROUND, markers=("tpl_result_ok",)),
    ScreenSpec(State.GAMEPLAY, markers=("tpl_pause",)),
    ScreenSpec(State.START_3, markers=("tpl_double_coins",)),
    # START_1: Buy-Upgrades panel present AND Double Coins banner absent.
    ScreenSpec(State.START_1, markers=("tpl_buy_upgrades",), absent=("tpl_double_coins",)),
    ScreenSpec(State.MAIN_MENU, markers=("tpl_main_marker",)),
]

# Templates to tap (center of match) for each actionable state, in order.
# GAMEPLAY (anchor, MacroPlayer takes over) and CAPTCHA (solver) have no plan here.
TAP_PLAN: dict[str, tuple[str, ...]] = {
    State.MAIN_MENU: ("tpl_play_main",),                 # -> START_1
    State.START_1: ("tpl_pink_box", "tpl_multi_icon"),   # tap box, then Multi
    State.START_2: ("tpl_multibuy",),                    # Multi-Buy
    State.START_3: ("tpl_play_start",),                  # Play -> replay (own button)
    State.END_ROUND: ("tpl_result_ok",),                 # OK
    State.END_BOX: ("tpl_open_all",),                    # Open all
    State.END_BOX_OPEN: ("tpl_box_confirm",),            # Confirm
    State.LEVEL_UP: ("tpl_levelup_confirm",),            # Confirm
    State.MENU_REWARD: ("tpl_congrats_confirm",),        # Confirm
    State.MONEY_POPUP: ("tpl_cancel",),                  # Cancel (then STOP)
    State.CONN_LOST: ("tpl_conn_confirm",),              # Confirm (retry connection)
    State.FRIEND_INFO: ("tpl_friend_close",),            # X (close friend popup)
    State.DAILY_CHECKIN: ("tpl_daily_ok",),              # OK (dismiss daily check-in)
}
