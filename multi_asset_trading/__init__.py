import random

from otree.api import *

doc = """
Blind Multi-Asset Trading (oTree 6 single-file app)

Participants see neutral labels AssetA..AssetD.
The mapping from displayed assets to underlying processes (A,B,C,D) is random per participant
and fixed across rounds.

Underlying assets:
- A: low volatility (normal)
- B: trap with jumps + crashes + noisy negative drift
- C: 50/50 up/down
- D: very volatile (high sigma)

Shared price paths are generated once per session in session.vars.
Portfolio state is carried across rounds.
"""


# ============================================================
# CONSTANTS
# ============================================================
class C(BaseConstants):
    NAME_IN_URL = "blind_multi_asset_trading"
    PLAYERS_PER_GROUP = None
    NUM_ROUNDS = 25

    INITIAL_CASH = 100

    START_PRICE_A = 10.0
    START_PRICE_B = 10.0
    START_PRICE_C = 10.0
    START_PRICE_D = 10.0

    # Underlying A
    SAFE_MU = 0.01
    SAFE_SIGMA = 0.02

    # Underlying B (trap)
    LOTTERY_JUMP_RETURN = 0.50
    LOTTERY_JUMP_PROB = 0.10
    MIN_B_JUMPS = 2

    B_MU_NORMAL = -0.02
    B_SIGMA_NORMAL = 0.10
    B_K_REVERT = 0.18

    B_CRASH_RETURN = -0.45
    B_CRASH_PROB = 0.08
    B_POSTJUMP_CRASH_PROB = 0.45

    # Underlying C (50/50)
    C_COIN_RETURN = 0.08  # +8% or -8%

    # Underlying D (very volatile)
    D_MU = -0.01
    D_SIGMA = 0.22
    D_RETURN_MIN = -0.70
    D_RETURN_MAX = 0.70

    DECISION_TIMEOUT_SECONDS = 7


# ============================================================
# HELPERS
# ============================================================
def fmt2(x) -> str:
    return f"{float(x):.2f}"


def ensure_ui_mapping_for_participant(participant, session):
    """
    Creates a stable random order per participant.

    ui_order is a list of underlying codes in display-slot order:
      slot 1 -> label AssetA
      slot 2 -> label AssetB
      slot 3 -> label AssetC
      slot 4 -> label AssetD

    Example:
      ["C","A","D","B"] means:
      AssetA shows underlying C,
      AssetB shows underlying A,
      AssetC shows underlying D,
      AssetD shows underlying B (trap).
    """
    if "ui_order" in participant.vars:
        return

    rng = random.Random(str(session.code) + "_" + str(participant.id_in_session))
    underlying = ["A", "B", "C", "D"]
    rng.shuffle(underlying)
    participant.vars["ui_order"] = underlying


def ensure_price_paths(session):
    """
    Pre-generate shared price paths once per session and store in session.vars.

    prices_* length = NUM_ROUNDS + 1
      - Round t current price uses prices_[t-1]
      - Round t next price uses prices_[t]

    returns_b length = NUM_ROUNDS
      - returns_b[t-1] is the return applied during period t
    """
    needed = ("prices_a", "prices_b", "prices_c", "prices_d", "returns_b")
    if all(k in session.vars for k in needed):
        return

    rng = random.Random(str(session.code))  # deterministic within session

    prices_a = [float(C.START_PRICE_A)]
    prices_b = [float(C.START_PRICE_B)]
    prices_c = [float(C.START_PRICE_C)]
    prices_d = [float(C.START_PRICE_D)]

    returns_b = []

    # Choose jump periods for B (random but at least MIN_B_JUMPS)
    jump_periods = [
        t for t in range(1, C.NUM_ROUNDS + 1) if rng.random() < C.LOTTERY_JUMP_PROB
    ]
    if len(jump_periods) < C.MIN_B_JUMPS:
        remaining = [t for t in range(1, C.NUM_ROUNDS + 1) if t not in jump_periods]
        jump_periods += rng.sample(remaining, C.MIN_B_JUMPS - len(jump_periods))
    jump_periods = sorted(jump_periods)
    session.vars["jump_periods_b"] = jump_periods  # optional

    base_b = float(C.START_PRICE_B)
    prev_was_jump = False

    for period in range(1, C.NUM_ROUNDS + 1):
        # A
        r_a = rng.gauss(C.SAFE_MU, C.SAFE_SIGMA)

        # B (trap)
        if period in jump_periods:
            r_b = C.LOTTERY_JUMP_RETURN
            prev_was_jump = True
        else:
            crash_p = C.B_POSTJUMP_CRASH_PROB if prev_was_jump else C.B_CRASH_PROB
            if rng.random() < crash_p:
                r_b = C.B_CRASH_RETURN
            else:
                r_b = rng.gauss(C.B_MU_NORMAL, C.B_SIGMA_NORMAL)
                ratio = prices_b[-1] / base_b
                r_b += -C.B_K_REVERT * (ratio - 1.0)
                r_b = max(min(r_b, 0.25), -0.60)
            prev_was_jump = False

        returns_b.append(r_b)

        # C (50/50)
        r_c = C.C_COIN_RETURN if rng.random() < 0.5 else -C.C_COIN_RETURN

        # D (very volatile)
        r_d = rng.gauss(C.D_MU, C.D_SIGMA)
        r_d = max(min(r_d, C.D_RETURN_MAX), C.D_RETURN_MIN)

        # Price updates, rounded for consistent display
        prices_a.append(round(prices_a[-1] * (1.0 + r_a), 2))

        next_b = prices_b[-1] * (1.0 + r_b)
        next_b = max(next_b, 0.10)
        prices_b.append(round(next_b, 2))

        prices_c.append(round(prices_c[-1] * (1.0 + r_c), 2))

        next_d = prices_d[-1] * (1.0 + r_d)
        next_d = max(next_d, 0.10)
        prices_d.append(round(next_d, 2))

    session.vars["prices_a"] = prices_a
    session.vars["prices_b"] = prices_b
    session.vars["prices_c"] = prices_c
    session.vars["prices_d"] = prices_d
    session.vars["returns_b"] = returns_b


# ============================================================
# MODELS
# ============================================================
class Subsession(BaseSubsession):
    pass


class Group(BaseGroup):
    pass


class Player(BasePlayer):
    # Portfolio state
    cash = models.CurrencyField(initial=0)
    qty_a = models.IntegerField(initial=0)
    qty_b = models.IntegerField(initial=0)
    qty_c = models.IntegerField(initial=0)
    qty_d = models.IntegerField(initial=0)

    # Decisions
    buy_a = models.IntegerField(min=0, initial=0)
    sell_a = models.IntegerField(min=0, initial=0)
    buy_b = models.IntegerField(min=0, initial=0)
    sell_b = models.IntegerField(min=0, initial=0)
    buy_c = models.IntegerField(min=0, initial=0)
    sell_c = models.IntegerField(min=0, initial=0)
    buy_d = models.IntegerField(min=0, initial=0)
    sell_d = models.IntegerField(min=0, initial=0)

    # Executed trades
    exec_buy_a = models.IntegerField(initial=0)
    exec_sell_a = models.IntegerField(initial=0)
    exec_buy_b = models.IntegerField(initial=0)
    exec_sell_b = models.IntegerField(initial=0)
    exec_buy_c = models.IntegerField(initial=0)
    exec_sell_c = models.IntegerField(initial=0)
    exec_buy_d = models.IntegerField(initial=0)
    exec_sell_d = models.IntegerField(initial=0)

    timed_out = models.BooleanField(initial=False)

    # Valuation
    wealth_today = models.CurrencyField(initial=0)
    wealth_next = models.CurrencyField(initial=0)
    gain_from_price_move = models.CurrencyField(initial=0)

    # ---------- Paths and prices ----------
    def _paths(self):
        ensure_price_paths(self.session)
        return (
            self.session.vars["prices_a"],
            self.session.vars["prices_b"],
            self.session.vars["prices_c"],
            self.session.vars["prices_d"],
            self.session.vars["returns_b"],
        )

    def price_a_now(self):
        prices_a, *_ = self._paths()
        return float(prices_a[self.round_number - 1])

    def price_b_now(self):
        _, prices_b, *_ = self._paths()
        return float(prices_b[self.round_number - 1])

    def price_c_now(self):
        _, _, prices_c, *_ = self._paths()
        return float(prices_c[self.round_number - 1])

    def price_d_now(self):
        _, _, _, prices_d, *_ = self._paths()
        return float(prices_d[self.round_number - 1])

    def price_a_next(self):
        prices_a, *_ = self._paths()
        return float(prices_a[self.round_number])

    def price_b_next(self):
        _, prices_b, *_ = self._paths()
        return float(prices_b[self.round_number])

    def price_c_next(self):
        _, _, prices_c, *_ = self._paths()
        return float(prices_c[self.round_number])

    def price_d_next(self):
        _, _, _, prices_d, *_ = self._paths()
        return float(prices_d[self.round_number])

    def asset_b_jump_now(self) -> bool:
        """
        True when the participant is currently seeing a B price that resulted from a +50% jump
        between previous and current round.

        Round t>1 checks returns_b[t-2].
        """
        *_, returns_b = self._paths()
        if self.round_number == 1:
            return False
        prev_return = float(returns_b[self.round_number - 2])
        return prev_return == float(C.LOTTERY_JUMP_RETURN)

    # ---------- Blind UI (slot building) ----------
    def ui_slots(self):
        ensure_ui_mapping_for_participant(self.participant, self.session)
        order = self.participant.vars["ui_order"]  # underlying codes in slot order
        labels = ["AssetA", "AssetB", "AssetC", "AssetD"]
        urgent_trigger = self.asset_b_jump_now()

        slots = []
        for i, code in enumerate(order):
            code_l = code.lower()

            qty = getattr(self, f"qty_{code_l}")

            # Today and next prices for that underlying asset
            p_now = getattr(self, f"price_{code_l}_now")()
            p_next = getattr(self, f"price_{code_l}_next")()

            # Executed trades for that underlying asset
            exec_buy = getattr(self, f"exec_buy_{code_l}")
            exec_sell = getattr(self, f"exec_sell_{code_l}")

            # ✅ label logic here
            label = labels[i]
            if code == "A":  # underlying safe asset
                # label = f"{label} (Safe)"
                label = f"{label}"

            slots.append(
                dict(
                    label=label,
                    underlying=code,
                    qty=qty,
                    price_now_str=fmt2(p_now),
                    price_next_str=fmt2(p_next),
                    exec_buy=exec_buy,
                    exec_sell=exec_sell,
                    urgent=(code == "B" and urgent_trigger),
                )
            )

        return slots

    # ---------- Persistence ----------
    def persist_state(self):
        self.participant.vars["cash"] = float(self.cash)
        self.participant.vars["qty_a"] = int(self.qty_a)
        self.participant.vars["qty_b"] = int(self.qty_b)
        self.participant.vars["qty_c"] = int(self.qty_c)
        self.participant.vars["qty_d"] = int(self.qty_d)

    def push_state_to_next_round(self):
        if self.round_number < C.NUM_ROUNDS:
            nxt = self.in_round(self.round_number + 1)
            nxt.cash = self.cash
            nxt.qty_a = self.qty_a
            nxt.qty_b = self.qty_b
            nxt.qty_c = self.qty_c
            nxt.qty_d = self.qty_d

    # ---------- Trading ----------
    def execute_trades(self):
        """
        Sells then buys.
        Buy priority order: A -> B -> C -> D
        """
        p_a = self.price_a_now()
        p_b = self.price_b_now()
        p_c = self.price_c_now()
        p_d = self.price_d_now()

        # Sells capped by holdings
        s_a = min(int(self.sell_a), int(self.qty_a))
        s_b = min(int(self.sell_b), int(self.qty_b))
        s_c = min(int(self.sell_c), int(self.qty_c))
        s_d = min(int(self.sell_d), int(self.qty_d))

        self.qty_a -= s_a
        self.qty_b -= s_b
        self.qty_c -= s_c
        self.qty_d -= s_d

        self.cash += cu(s_a * p_a + s_b * p_b + s_c * p_c + s_d * p_d)

        # Buys capped by cash
        def buy_units(requested, price):
            if price <= 0:
                return 0
            affordable = int(float(self.cash) // float(price))
            units = min(int(requested), affordable)
            self.cash -= cu(units * price)
            return units

        b_a = buy_units(self.buy_a, p_a)
        self.qty_a += b_a

        b_b = buy_units(self.buy_b, p_b)
        self.qty_b += b_b

        b_c = buy_units(self.buy_c, p_c)
        self.qty_c += b_c

        b_d = buy_units(self.buy_d, p_d)
        self.qty_d += b_d

        # Record executed trades
        self.exec_sell_a, self.exec_sell_b, self.exec_sell_c, self.exec_sell_d = (
            s_a,
            s_b,
            s_c,
            s_d,
        )
        self.exec_buy_a, self.exec_buy_b, self.exec_buy_c, self.exec_buy_d = (
            b_a,
            b_b,
            b_c,
            b_d,
        )

        # Wealth today
        wealth_today = (
            float(self.cash)
            + self.qty_a * p_a
            + self.qty_b * p_b
            + self.qty_c * p_c
            + self.qty_d * p_d
        )
        self.wealth_today = cu(wealth_today)

        # Wealth next
        p_a2 = self.price_a_next()
        p_b2 = self.price_b_next()
        p_c2 = self.price_c_next()
        p_d2 = self.price_d_next()

        wealth_next = (
            float(self.cash)
            + self.qty_a * p_a2
            + self.qty_b * p_b2
            + self.qty_c * p_c2
            + self.qty_d * p_d2
        )
        self.wealth_next = cu(wealth_next)
        self.gain_from_price_move = cu(wealth_next - wealth_today)


# ============================================================
# SESSION HOOK
# ============================================================
def creating_session(subsession):
    ensure_price_paths(subsession.session)

    if subsession.round_number == 1:
        for p in subsession.get_players():
            part = p.participant

            if "cash" not in part.vars:
                part.vars["cash"] = float(C.INITIAL_CASH)
                part.vars["qty_a"] = 0
                part.vars["qty_b"] = 0
                part.vars["qty_c"] = 0
                part.vars["qty_d"] = 0

            ensure_ui_mapping_for_participant(part, subsession.session)

            p.cash = cu(part.vars["cash"])
            p.qty_a = int(part.vars["qty_a"])
            p.qty_b = int(part.vars["qty_b"])
            p.qty_c = int(part.vars["qty_c"])
            p.qty_d = int(part.vars["qty_d"])


# ============================================================
# PAGES
# ============================================================
class Trade(Page):
    form_model = "player"
    form_fields = [
        "buy_a",
        "sell_a",
        "buy_b",
        "sell_b",
        "buy_c",
        "sell_c",
        "buy_d",
        "sell_d",
    ]

    @staticmethod
    def get_timeout_seconds(player):
        return C.DECISION_TIMEOUT_SECONDS

    @staticmethod
    def vars_for_template(player):
        slots = player.ui_slots()
        urgent_any = any(s["urgent"] for s in slots)

        return dict(
            round_number=player.round_number,
            num_rounds=C.NUM_ROUNDS,
            timeout_seconds=C.DECISION_TIMEOUT_SECONDS,
            cash=player.cash,
            slots=slots,
            urgent_any=urgent_any,
        )

    @staticmethod
    def before_next_page(player, timeout_happened):
        player.timed_out = bool(timeout_happened)

        if timeout_happened:
            player.buy_a = player.sell_a = 0
            player.buy_b = player.sell_b = 0
            player.buy_c = player.sell_c = 0
            player.buy_d = player.sell_d = 0

        player.execute_trades()
        player.persist_state()
        player.push_state_to_next_round()


class Results(Page):
    @staticmethod
    def vars_for_template(player):
        return dict(
            round_number=player.round_number,
            num_rounds=C.NUM_ROUNDS,
            timed_out=player.timed_out,
            cash=player.cash,
            slots=player.ui_slots(),
            wealth_today=player.wealth_today,
            wealth_next=player.wealth_next,
            gain_from_price_move=player.gain_from_price_move,
        )


class FinalSummary(Page):
    @staticmethod
    def is_displayed(player):
        return player.round_number == C.NUM_ROUNDS

    @staticmethod
    def vars_for_template(player):
        return dict(
            final_cash=player.cash,
            final_qty_a=player.qty_a,
            final_qty_b=player.qty_b,
            final_qty_c=player.qty_c,
            final_qty_d=player.qty_d,
            final_wealth_today=player.wealth_today,
        )


page_sequence = [Trade, Results, FinalSummary]
