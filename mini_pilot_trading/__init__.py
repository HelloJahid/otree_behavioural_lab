import random

from otree.api import *

doc = "Module 4: trading decisions (buy/sell) + trade execution + carry-over"


# ============================================================
# CONSTANTS
# ============================================================
class C(BaseConstants):
    NAME_IN_URL = "mini_pilot_trading"
    PLAYERS_PER_GROUP = None
    NUM_ROUNDS = 10

    # Endowment
    INITIAL_CASH = 100

    # Initial price for assest(Round 1)
    START_PRICE_A = 10.0
    START_PRICE_B = 10.0

    # Assest A returns: Normal(mu, sigma)
    SAFE_MU = 0.01
    SAFE_SIGMA = 0.02

    # Assest B returns: mostly down, rare jump
    LOTTERY_DOWN_RETURN = -0.02
    LOTTERY_JUMP_RETURN = 0.50
    LOTTERY_JUMP_PROB = 0.10

    # Optional time limit for learning (change or set to None)
    DECISION_TIMEOUT_SECONDS = 25


def ensure_price_paths(session):
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


def fmt2(x):
    return f"{float(x):.2f}"


class Subsession(BaseSubsession):
    pass


class Group(BaseGroup):
    pass


# ============================================================
# PLAYER
# ============================================================
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
            price_a_str=fmt2(player.price_a_now()),
            price_b_str=fmt2(player.price_b_now()),
            cash=player.cash,
            qty_a=player.qty_a,
            qty_b=player.qty_b,
            exec_buy_a=player.exec_buy_a,
            exec_sell_a=player.exec_sell_a,
            exec_buy_b=player.exec_buy_b,
            exec_sell_b=player.exec_sell_b,
            wealth_now=player.wealth_now,
            price_a_next_str=fmt2(player.price_a_next()),
            price_b_next_str=fmt2(player.price_b_next()),
            wealth_next=player.wealth_next,
            gain_from_prices=player.gain_from_prices,
        )


page_sequence = [Trade, Results]
