import random

from otree.api import *

doc = """
Mini-Pilot Trading Experiment (oTree 6, single-file app)

Core requirements implemented:
- 10 rounds
- initial cash endowment
- two assets with pre-generated shared price paths stored in session.vars
- wealth carried over via participant.vars
- hard decision timer
- urgency cue when Asset B has just jumped up (+50%) from previous round to current round
"""


# ============================================================
# CONSTANTS
# ============================================================
class C(BaseConstants):
    NAME_IN_URL = "mini_pilot_trading"
    PLAYERS_PER_GROUP = None
    NUM_ROUNDS = 20

    # Endowment
    INITIAL_CASH = 100

    # Initial price for assest(Round 1)
    START_PRICE_A = 10.0
    START_PRICE_B = 10.0

    # Assest A returns: Normal(mu, sigma)
    SAFE_MU = 0.01
    SAFE_SIGMA = 0.02

    # Assest B returns: mostly down, rare jump
    # LOTTERY_DOWN_RETURN = -0.02
    # LOTTERY_JUMP_RETURN = 0.50
    # LOTTERY_JUMP_PROB = 0.10
    # Asset B: trap dynamics (pump and dump)
    MIN_B_JUMPS = 2  # at least 2 urgency traps in 10 rounds
    B_BASE_MU = -0.03  # negative drift most of the time
    B_BASE_SIGMA = 0.06  # volatility even when not jumping
    B_CRASH_RETURN = -0.60  # occasional big crash
    B_CRASH_PROB = 0.10  # crash chance in normal periods
    B_POSTJUMP_CRASH_PROB = 0.70  # crash chance immediately after a jump

    # Recovery behaviour when B is below the base price
    B_RECOVERY_MU = 0.03  # small positive drift for rebounds
    B_RECOVERY_SIGMA = 0.03  # modest volatility during recovery
    B_RECOVERY_MAX_RETURN = 0.10  # prevent big rebound in one period

    # Keep B mostly below base price in normal periods
    B_CAP_MULTIPLIER = 0.99  # cap B to 99% of START_PRICE_B (stays below base)

    LOTTERY_JUMP_RETURN = 0.50
    LOTTERY_JUMP_PROB = 0.10
    LOTTERY_DOWN_RETURN = -0.02  # you can keep it but it will no longer drive B

    # Optional time limit for learning (change or set to None)
    DECISION_TIMEOUT_SECONDS = 7


# ============================================================
# HELPERS
# ============================================================
def fmt2(x) -> str:
    """Format a number to 2 decimals as a string (avoids template filters)."""
    return f"{float(x):.2f}"


def ensure_price_paths_past(session):
    """
    Create shared price paths once per session and store in session.vars
    Safe to call multiple times
    """

    if all(k in session.vars for k in ("prices_a", "prices_b", "returns_b")):
        return

    rng = random.Random(str(session.code))  # determinitic within the session

    prices_a = [float(C.START_PRICE_A)]
    prices_b = [float(C.START_PRICE_B)]
    returns_b = []  # length = NUM_ROUNDS

    # Loop exactly  NUM_ROUNDS = 10
    # Each loop generates one “period” of returns and updates prices to the next period.
    for r_number in range(C.NUM_ROUNDS):
        r_a = rng.gauss(C.SAFE_MU, C.SAFE_SIGMA)

        jump = rng.random() < C.LOTTERY_JUMP_PROB
        r_b = C.LOTTERY_JUMP_RETURN if jump else C.LOTTERY_DOWN_RETURN
        returns_b.append(r_b)

        # Price update rule: P_{t+1} = P_t * (1 + r)
        prices_a.append(round(prices_a[-1] * (1.0 + r_a), 2))
        prices_b.append(round(prices_b[-1] * (1.0 + r_b), 2))

        print(f"DEBUG INFO Round-{r_number}: r_a:{r_a}, jump:{jump}, r_b:{r_b}")

    print("Round end. \n")
    # store the price
    session.vars["prices_a"] = prices_a
    session.vars["prices_b"] = prices_b
    session.vars["returns_b"] = returns_b


def ensure_price_paths(session):
    """
    Shared price paths (one per session).

    Asset A: Normal(mu, sigma)
    Asset B: lottery-trap with genuine unpredictability:
      - at least MIN_B_JUMPS true jumps per session (random placement)
      - jump = +50%
      - after a jump, crash is more likely (trap)
      - otherwise: noisy daily moves with negative drift
      - mild mean-reversion around base price to avoid monotone collapse
        (still negative expected value overall)
    """

    if all(k in session.vars for k in ("prices_a", "prices_b", "returns_b")):
        return

    rng = random.Random(str(session.code))  # deterministic within the session

    prices_a = [float(C.START_PRICE_A)]
    prices_b = [float(C.START_PRICE_B)]
    returns_b = []

    # ------------------------------------------------------------
    # Choose jump periods for B: random, but at least MIN_B_JUMPS
    # Periods are 1..NUM_ROUNDS
    # ------------------------------------------------------------
    jump_periods = [
        t for t in range(1, C.NUM_ROUNDS + 1) if rng.random() < C.LOTTERY_JUMP_PROB
    ]
    if len(jump_periods) < C.MIN_B_JUMPS:
        remaining = [t for t in range(1, C.NUM_ROUNDS + 1) if t not in jump_periods]
        jump_periods += rng.sample(remaining, C.MIN_B_JUMPS - len(jump_periods))
    jump_periods = sorted(jump_periods)
    session.vars["jump_periods_b"] = jump_periods  # optional debug

    # ------------------------------------------------------------
    # Parameters (tune here)
    # ------------------------------------------------------------
    BASE = float(C.START_PRICE_B)

    # Normal periods (no jump / no crash): negative drift but noisy
    MU_NORMAL = -0.02
    SIGMA_NORMAL = 0.10

    # Crash behaviour: occasional, and more likely right after a jump
    CRASH_RETURN = -0.45
    CRASH_PROB = 0.08
    POSTJUMP_CRASH_PROB = 0.45

    # Mean reversion strength:
    # When price is above base, push returns downward a bit
    # When price is far below base, allow some pull-up (but not too strong)
    K_REVERT = 0.18  # 0.10 to 0.25 are reasonable

    prev_was_jump = False

    for period in range(1, C.NUM_ROUNDS + 1):
        # Asset A return
        r_a = rng.gauss(C.SAFE_MU, C.SAFE_SIGMA)

        # ----- Asset B return -----
        if period in jump_periods:
            r_b = C.LOTTERY_JUMP_RETURN
            prev_was_jump = True
        else:
            crash_p = POSTJUMP_CRASH_PROB if prev_was_jump else CRASH_PROB

            if rng.random() < crash_p:
                r_b = CRASH_RETURN
            else:
                # Noisy normal move
                r_b = rng.gauss(MU_NORMAL, SIGMA_NORMAL)

                # Mean reversion term (soft, price-dependent)
                # ratio > 1 means above base => negative adjustment
                # ratio < 1 means below base => positive adjustment
                ratio = prices_b[-1] / BASE
                r_b += -K_REVERT * (ratio - 1.0)

                # Keep returns within a sensible band (avoid extreme spikes from noise)
                r_b = max(min(r_b, 0.25), -0.60)

            prev_was_jump = False

        returns_b.append(r_b)

        # ----- Price updates with rounding for UI consistency -----
        next_a = prices_a[-1] * (1.0 + r_a)
        prices_a.append(round(next_a, 2))

        next_b = prices_b[-1] * (1.0 + r_b)
        next_b = max(next_b, 0.10)  # floor to avoid zero/negative price
        prices_b.append(round(next_b, 2))

        print(
            f"DEBUG period {period}: "
            f"r_a={r_a:.4f}, "
            f"B_jump={period in jump_periods}, "
            f"r_b={r_b:.4f}, "
            f"P_b_next={prices_b[-1]:.2f}"
        )

    session.vars["prices_a"] = prices_a
    session.vars["prices_b"] = prices_b
    session.vars["returns_b"] = returns_b


def ensure_urgency_rounds(session):
    """
    Decide in advance which rounds will show the urgency cue for Asset B.
    Random but deterministic within session, and at least 2 rounds in total.
    Stored in session.vars so all players see the same urgency schedule.
    """
    if "urgent_rounds_b" in session.vars:
        return

    rng = random.Random(str(session.code) + "_urgency")

    # Candidate rounds for showing urgency.
    # Round 1 cannot show urgency because there is no previous round.
    candidates = list(range(2, C.NUM_ROUNDS + 1))

    # First draw using probability (keeps it random in spirit)
    urgent = [r for r in candidates if rng.random() < C.LOTTERY_JUMP_PROB]

    # Enforce minimum 2 urgency rounds
    if len(urgent) < 2:
        remaining = [r for r in candidates if r not in urgent]
        urgent += rng.sample(remaining, 2 - len(urgent))

    urgent = sorted(urgent)
    session.vars["urgent_rounds_b"] = urgent


# ============================================================
# MODELS
# ============================================================
class Subsession(BaseSubsession):
    pass


class Group(BaseGroup):
    pass


# ============================================================
# PLAYER
class Player(BasePlayer):
    # ----------------------------
    # Persistent portfolio state
    # ----------------------------
    cash = models.CurrencyField(initial=0)
    qty_a = models.IntegerField(initial=0)
    qty_b = models.IntegerField(initial=0)

    # ----------------------------
    # Decisions entered by participant (each round)
    # ----------------------------
    buy_a = models.IntegerField(min=0, initial=0, label="Buy units of Asset A")
    sell_a = models.IntegerField(min=0, initial=0, label="Sell units of Asset A")
    buy_b = models.IntegerField(min=0, initial=0, label="Buy units of Asset B")
    sell_b = models.IntegerField(min=0, initial=0, label="Sell units of Asset B")

    # ----------------------------
    # Executed trades (after feasibility caps)
    # Participant requests sell_a = 10, But owns only qty_a = 3 -> Then exec_sell_a becomes 3, not 10
    # ----------------------------
    exec_buy_a = models.IntegerField(initial=0)
    exec_sell_a = models.IntegerField(initial=0)
    exec_buy_b = models.IntegerField(initial=0)
    exec_sell_b = models.IntegerField(initial=0)

    timed_out = models.BooleanField(initial=False)

    # ----------------------------
    # Outcome summary (optional, useful for results page / export)
    # wealth = cash + qty_a * p_a + qty_b * p_b
    # ----------------------------
    wealth_now = models.CurrencyField(initial=0)

    # Value of the same portfolio at next period prices (P_{t+1})
    wealth_next = models.CurrencyField(initial=0)

    # Change in wealth caused by price movement from t to t+1
    gain_from_prices = models.CurrencyField(initial=0)

    def _paths(self):
        ensure_price_paths(self.session)
        return (
            self.session.vars["prices_a"],
            self.session.vars["prices_b"],
            self.session.vars["returns_b"],
        )

    def price_a_now(self):
        prices_a, _, _ = self._paths()
        return prices_a[self.round_number - 1]

    def price_b_now(self):
        _, prices_b, _ = self._paths()
        return prices_b[self.round_number - 1]

    def price_a_next(self):
        prices_a, _, _ = self._paths()
        return prices_a[self.round_number]  # P_{t+1}

    def price_b_next(self):
        _, prices_b, _ = self._paths()
        return prices_b[self.round_number]  # P_{t+1}

    # ---------- Core trade logic ----------
    def execute_trades_and_carry_forward(self):
        """
        Execute trades as a price taker.

        Rules implemented:
        1) Sell orders first (cannot sell more than holdings, no short selling)
        2) Buy orders second (cannot spend more cash than available)
        3) Update cash and holdings
        4) Carry state into the next round's Player row
        """

        p_a = float(self.price_a_now())
        p_b = float(self.price_b_now())

        # ----------------------------
        # 1) SELL first (cap by holdings)
        # ----------------------------
        self.exec_sell_a = min(int(self.sell_a), int(self.qty_a))
        self.exec_sell_b = min(int(self.sell_b), int(self.qty_b))

        self.qty_a -= self.exec_sell_a
        self.qty_b -= self.exec_sell_b

        self.cash += cu(self.exec_sell_a * p_a + self.exec_sell_b * p_b)

        # ----------------------------
        # 2) BUY second (cap by cash)
        # ----------------------------
        max_buy_a = int(float(self.cash) // p_a) if p_a > 0 else 0
        self.exec_buy_a = min(int(self.buy_a), max_buy_a)
        self.cash -= cu(self.exec_buy_a * p_a)
        self.qty_a += self.exec_buy_a

        max_buy_b = int(float(self.cash) // p_b) if p_b > 0 else 0
        self.exec_buy_b = min(int(self.buy_b), max_buy_b)
        self.cash -= cu(self.exec_buy_b * p_b)
        self.qty_b += self.exec_buy_b

        # ----------------------------
        # 3) Wealth at current prices (after trades)
        # ----------------------------
        self.wealth_now = cu(float(self.cash) + self.qty_a * p_a + self.qty_b * p_b)

        # Wealth at next period prices (P_{t+1})
        p_a_next = float(self.price_a_next())
        p_b_next = float(self.price_b_next())

        self.wealth_next = cu(
            float(self.cash) + self.qty_a * p_a_next + self.qty_b * p_b_next
        )

        # Pure gain from prices, holdings and cash fixed
        self.gain_from_prices = self.wealth_next - self.wealth_now

        # ----------------------------
        # 4) Persist to participant.vars (true state)
        # ----------------------------
        self.participant.vars["cash"] = float(self.cash)
        self.participant.vars["qty_a"] = int(self.qty_a)
        self.participant.vars["qty_b"] = int(self.qty_b)

        # ----------------------------
        # 5) Push into next round's Player row (carry-over snapshot)
        # ----------------------------
        if self.round_number < C.NUM_ROUNDS:
            nxt = self.in_round(self.round_number + 1)
            nxt.cash = self.cash
            nxt.qty_a = self.qty_a
            nxt.qty_b = self.qty_b

    def asset_b_jump_now(self) -> bool:
        """
        Urgency cue should light up when the participant is currently seeing
        a price that resulted from a +50% jump from the previous round.

        Round 1 has no previous round, so it is False.
        Round t>1 checks returns_b[t-2].
        """
        _, _, returns_b = self._paths()
        if self.round_number == 1:
            return False
        # prev_return = float(returns_b[self.round_number - 2])
        # return prev_return == float(C.LOTTERY_JUMP_RETURN)
        ensure_urgency_rounds(self.session)
        return self.round_number in self.session.vars["urgent_rounds_b"]


# ============================================================
# SESSION INITIALISATION
# ============================================================
# In oTree 6 single file apps, creating_session is a module level hook function.
# oTree calls it automatically when a session is created and initialised.
def creating_session(subsession):
    """
    oTree 6 single-file hook.
    Only initialise Round 1. Later rounds are updated by carry-forward.
    """
    ensure_price_paths(subsession.session)

    if subsession.round_number == 1:
        for p in subsession.get_players():
            part = p.participant
            if "cash" not in part.vars:
                part.vars["cash"] = float(C.INITIAL_CASH)
                part.vars["qty_a"] = 0
                part.vars["qty_b"] = 0

            p.cash = cu(part.vars["cash"])
            p.qty_a = int(part.vars["qty_a"])
            p.qty_b = int(part.vars["qty_b"])


# ============================================================
# PAGES
# ============================================================
class Trade(Page):
    form_model = "player"
    form_fields = ["buy_a", "sell_a", "buy_b", "sell_b"]

    @staticmethod
    def get_timeout_seconds(player):
        return C.DECISION_TIMEOUT_SECONDS

    # vars_for_template is a standard oTree method.
    # It returns variables that will be available inside the template as {{ ... }}.
    # It is a staticmethod because oTree passes in the player directly and you do not need self.
    @staticmethod
    def vars_for_template(player):
        return dict(
            round_number=player.round_number,
            num_rounds=C.NUM_ROUNDS,
            cash=player.cash,
            qty_a=player.qty_a,
            qty_b=player.qty_b,
            price_a_str=fmt2(player.price_a_now()),
            price_b_str=fmt2(player.price_b_now()),
            urgent_b=player.asset_b_jump_now(),
        )

    # before_next_page runs after the participant clicks Next.
    # At this point player.spend has been stored by oTree.
    # You call apply_spend_and_persist, which:reduces player.cash and writes updated state into participant.vars
    # That ensures the next round starts with updated cash.
    @staticmethod
    def before_next_page(player, timeout_happened):
        player.timed_out = bool(timeout_happened)
        # If timeout happens, oTree keeps defaults (all 0), so this still works.
        player.execute_trades_and_carry_forward()


class Results(Page):
    @staticmethod
    def vars_for_template(player):
        return dict(
            round_number=player.round_number,
            num_rounds=C.NUM_ROUNDS,
            timed_out=player.timed_out,
            price_a_today=fmt2(player.price_a_now()),
            price_b_today=fmt2(player.price_b_now()),
            cash=player.cash,
            qty_a=player.qty_a,
            qty_b=player.qty_b,
            exec_buy_a=player.exec_buy_a,
            exec_sell_a=player.exec_sell_a,
            exec_buy_b=player.exec_buy_b,
            exec_sell_b=player.exec_sell_b,
            wealth_today=player.wealth_now,
            price_a_next=fmt2(player.price_a_next()),
            price_b_next=fmt2(player.price_b_next()),
            wealth_next=player.wealth_next,
            gain_from_price_move=player.gain_from_prices,
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
            final_wealth_today=player.wealth_now,
        )


page_sequence = [Trade, Results, FinalSummary]
