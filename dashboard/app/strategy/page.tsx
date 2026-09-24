import Link from "next/link";
import Term from "@/components/Term";
import ExampleCard from "@/components/ExampleCard";
import StrategyFlowDiagram from "@/components/StrategyFlowDiagram";
import { ShieldCheck, Layers, Ban, FlaskConical, ArrowRight, TrendingUp, TrendingDown } from "lucide-react";
import type { ReactNode } from "react";

export const metadata = {
  title: "Trading Strategy",
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-bg-border bg-bg-panel p-5">
      <h2 className="mb-3 text-base font-semibold text-white">{title}</h2>
      <div className="space-y-3 text-sm leading-relaxed text-muted">{children}</div>
    </section>
  );
}

function Side({
  tone,
  icon,
  title,
  subtitle,
  children,
}: {
  tone: "gain" | "loss";
  icon: ReactNode;
  title: string;
  subtitle: string;
  children: ReactNode;
}) {
  const toneClass = tone === "gain" ? "border-gain/30 bg-gain/[0.03]" : "border-loss/30 bg-loss/[0.03]";
  const textClass = tone === "gain" ? "text-gain" : "text-loss";
  return (
    <section className={`rounded-xl border p-5 ${toneClass}`}>
      <div className={`mb-1 flex items-center gap-2 text-base font-semibold ${textClass}`}>
        {icon} {title}
      </div>
      <p className="mb-4 text-sm text-muted">{subtitle}</p>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

function Rule({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-bg-border bg-bg-panel p-3.5 text-sm leading-relaxed text-muted">
      <div className="mb-1 font-semibold text-white">{title}</div>
      {children}
    </div>
  );
}

export default function StrategyPage() {
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-white">Trading Strategy</h1>
        <p className="text-sm text-muted">
          A plain-English explanation of what this bot does and why — no investing background required.
        </p>
      </div>

      <Section title="The objective">
        <p>
          The bot trades a fixed watch-list on Alpaca{" "}
          <Term definition="A simulated brokerage account. Orders are real in every way except no real money changes hands, which makes it safe for testing a strategy.">
            paper trading
          </Term>{" "}
          — every trade you see is simulated. It runs on a fixed schedule, follows the same rules every time, and
          reacts faster and more consistently than a human could to genuine signals, while hard risk limits keep
          any single bad call from doing much damage.
        </p>
        <p>
          Every decision splits cleanly into two independent halves:{" "}
          <span className="text-gain">buying</span> — deciding whether to open a position — and{" "}
          <span className="text-loss">selling</span> — deciding whether to close one it already holds. They run on
          different rules, shown separately below.
        </p>
      </Section>

      <Section title="How one trade flows, end to end">
        <StrategyFlowDiagram />
      </Section>

      <Side
        tone="gain"
        icon={<TrendingUp size={18} />}
        title="Buying — when a position opens"
        subtitle="Two independent signals look for an opportunity. Every real buy still has to clear the limits below it."
      >
        <Rule title="Path A — news-driven momentum (the only path currently trading for real)">
          Three things each earn partial credit toward one score out of 1.0: how positive the{" "}
          <Term definition="A number from -10 to +10 summarizing how positive or negative the recent news coverage is for a stock.">
            sentiment
          </Term>{" "}
          is (50%), how many headlines back it up, up to 5 (20%), and today&apos;s{" "}
          <Term definition="Today's trading volume compared to the recent 20-day average for this time of day. Above 1.0x means more shares are trading than usual - a sign the market has genuinely noticed the news.">
            volume ratio
          </Term>{" "}
          versus normal, up to 1.5x (30%). A combined score of 0.60+ buys — so one so-so leg no longer blocks an
          otherwise strong story. Two checks still can&apos;t be bought around regardless of score: already up
          more than 8% today, or trading below its own 20-day{" "}
          <Term definition="The average closing price over a recent period. Used to judge whether a stock is in a longer-term uptrend or downtrend.">
            moving average
          </Term>
          .
        </Rule>
        <Rule title="Path B — technical mean-reversion">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-amber-400">
            <FlaskConical size={13} /> Shadow mode — logged only, no order is placed
          </div>
          A different idea: instead of reacting to news, it looks for a stock that&apos;s healthy long-term but has
          dropped sharply short-term (an oversold-bounce setup). All four must hold: above its 200-day average,{" "}
          <Term definition="Relative Strength Index over a 2-day window: a 0-100 reading of how fast and far a stock has fallen short-term. Below 10 is an extreme, short-lived oversold reading.">
            2-day RSI
          </Term>{" "}
          at 10 or below, volume at least 1.3x normal, and sentiment no worse than -5. It would exit on its own
          rule too — RSI back above 65, or 5 days without reverting.
          <div className="mt-2">
            <Link
              href="/shadow-comparison"
              className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-400 hover:text-amber-300"
            >
              Compare Path B&apos;s shadow signals to what actually traded <ArrowRight size={12} />
            </Link>
          </div>
        </Rule>
        <Rule title="What can still block a buy, on either path">
          The market itself down sharply or below its 50-day average (both paths pause), the portfolio already
          down 4% today, the stock sold within the last 24 hours (
          <Term definition="A waiting period after selling a stock before the bot will consider buying it again, to avoid rapidly flip-flopping on noisy news.">
            cooldown
          </Term>
          ), or the position/sector/cash limits below.
        </Rule>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex items-center gap-2 rounded-lg border border-bg-border bg-bg-panel2 p-3 text-xs text-muted">
            <Layers size={16} className="shrink-0 text-gain" /> Max 5% per position, 50% total invested
          </div>
          <div className="flex items-center gap-2 rounded-lg border border-bg-border bg-bg-panel2 p-3 text-xs text-muted">
            <Ban size={16} className="shrink-0 text-gain" /> Max 10 open positions, 3/sector, 3 new per cycle
          </div>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <ExampleCard type="buy" title="Good-enough beats perfect">
            9 headlines on a strong earnings beat (sentiment +6.0, below the old +8 cutoff) with modest 1.4x
            volume. Sentiment contributes 0.30, headlines the full 0.20, volume 0.28 — a total of 0.78, well past
            0.60. The bot buys, with a stop-loss and take-profit attached immediately.
          </ExampleCard>
          <ExampleCard type="hold" title="Not enough to go on">
            Only 2 mildly positive headlines today, and the price is flat. Below the 5-headline bar for full
            credit, so the composite score falls short of 0.60. The bot holds and waits for the next cycle.
          </ExampleCard>
        </div>
      </Side>

      <Side
        tone="loss"
        icon={<TrendingDown size={18} />}
        title="Selling — when a position closes"
        subtitle="Independent, simple rules layered on top of each other. Any one of them firing is enough — the position doesn't need to fail every check."
      >
        <Rule title="Always on: stop-loss and take-profit">
          Every position sells the instant it falls <span className="text-loss">10% below entry</span> — a
          completely separate order, always watching, regardless of anything else below. Alongside it, one mode is
          picked the moment a position opens and never changes mid-trade: a flat{" "}
          <span className="text-gain">+20% take-profit</span> flag (the plain default), or — on this bot&apos;s
          current setting — a{" "}
          <Term definition="A protective exit that follows a position's price upward instead of selling at one fixed target, so a strong winner isn't capped at the same level as a modest one.">
            trailing stop
          </Term>
          : a distant 50% backstop, arming once the position is up 3%, then selling the moment price falls 7% from
          its <Term definition="The highest price a position has reached since it was bought.">peak</Term>.
        </Rule>
        <Rule title="Sentiment reversal (Path A positions)">
          Independent of price: a moderate negative reading (-5 or worse) sells early, but only with 3+ headlines
          behind it. A <span className="text-loss">severe</span> reading (-8 or worse) is trusted with just 1
          headline — waiting for more confirmation on an already-unambiguous story just rides the loss longer.
          Moderate-but-thin readings get no software action; the stop-loss/take-profit bracket manages it instead.
        </Rule>
        <Rule title="Time-based exit">
          A position that hasn&apos;t hit any rule above closes anyway after{" "}
          <span className="text-white">10 trading days</span> (Path A) or{" "}
          <span className="text-white">5 days</span> (Path B, if it goes live) — freeing the position and sector
          slot from a trade that&apos;s going nowhere.
        </Rule>
        <div className="grid gap-3 md:grid-cols-2">
          <ExampleCard type="sell" title="Sentiment sours, sells early">
            A stock bought two weeks ago on momentum gets 4 fresh headlines about a product recall — sentiment
            -6.5, clearing the 3-headline bar. Price hasn&apos;t dropped 10% yet, but the sentiment rule fires
            independently, so the bot sells rather than waiting for the stop-loss.
          </ExampleCard>
          <ExampleCard type="sell" title="Severe news needs no confirmation">
            One lawsuit headline drops sentiment straight to -9.0 — normally too thin at 1 headline, but severe
            enough (past -8) to act on immediately. Contrast: a milder -7.0 on 1 headline stays a hold — bad
            enough to flag, not severe enough to trust alone.
          </ExampleCard>
          <ExampleCard type="sell" title="Trailing stop locks in a big winner">
            Bought at $100 with the trailing stop on — no 20% flag, just a $90 stop-loss and $150 backstop. Price
            runs to a $140 peak, then slips to $130 (a 7% pullback) — sold there for a 30% gain, well past what
            the old flat +20% would have banked.
          </ExampleCard>
          <ExampleCard type="sell" title="Going nowhere gets closed on day 10">
            Bought at $50, then drifts $48-$52 for 10 trading days — never sours enough to trigger the sentiment
            exit, never near the stop-loss or take-profit. Closed at roughly breakeven, freeing the slot rather
            than sitting there indefinitely.
          </ExampleCard>
        </div>
      </Side>

      <Section title="Safety net at a glance">
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="flex items-center gap-2 rounded-lg border border-bg-border bg-bg-panel2 p-3 text-xs text-muted">
            <ShieldCheck size={16} className="shrink-0 text-loss" /> Stop-loss -10% on every position, no exceptions
          </div>
          <div className="flex items-center gap-2 rounded-lg border border-bg-border bg-bg-panel2 p-3 text-xs text-muted">
            <Ban size={16} className="shrink-0 text-loss" /> Auto-pause on new buys after a 4% daily loss
          </div>
          <div className="flex items-center gap-2 rounded-lg border border-bg-border bg-bg-panel2 p-3 text-xs text-muted">
            <Layers size={16} className="shrink-0 text-gain" /> Never more than 50% of the portfolio invested at once
          </div>
        </div>
      </Section>
    </div>
  );
}
