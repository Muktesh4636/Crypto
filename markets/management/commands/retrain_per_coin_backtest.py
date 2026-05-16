from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count

from markets.ml.model import PWM_MODEL_FAMILY, SignalModel, model_paths, resolve_model_paths
from markets.ml.retrain import retrain_all_symbols_from_backtest
from markets.models import PaperTrade
from markets.trading.constants import BACKTEST_NOTE_PREFIX


class Command(BaseCommand):
    help = (
        "Retrain each coin's model separately using only that coin's historical backtest trades "
        "(wins + losses, losses weighted higher)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--symbol", type=str, default="")
        parser.add_argument("--min-losses", type=int, default=3)
        parser.add_argument("--min-trades", type=int, default=10)
        parser.add_argument("--ensure-model", action="store_true", help="Create empty model if missing.")

    def handle(self, *args, **options):
        symbol = options["symbol"].strip().upper()
        min_losses = max(1, int(options["min_losses"]))
        min_trades = max(1, int(options["min_trades"]))
        ensure_model = bool(options["ensure_model"])

        if symbol:
            symbols = [symbol]
        else:
            symbols = list(
                PaperTrade.objects.filter(notes__startswith=BACKTEST_NOTE_PREFIX)
                .exclude(outcome=PaperTrade.Outcome.OPEN)
                .values("symbol")
                .annotate(n=Count("id"))
                .order_by("symbol")
                .values_list("symbol", flat=True)
            )

        if not symbols:
            self.stderr.write(self.style.ERROR("No historical backtest trades found in the journal."))
            return

        self.stdout.write(
            f"Per-coin retrain from backtest journal: {len(symbols)} symbol(s), "
            "each model uses ONLY its own trades."
        )

        if ensure_model:
            for sym in symbols:
                path, _ = resolve_model_paths(sym)
                if ensure_model and not path.exists():
                    path, _ = model_paths(sym, family=PWM_MODEL_FAMILY)
                if not path.exists():
                    model = SignalModel()
                    model.save({"symbol": sym, "training_mode": "placeholder"}, symbol=sym)
                    self.stdout.write(f"Created placeholder model for {sym}")

        result = retrain_all_symbols_from_backtest(
            symbols=symbols,
            min_losses=min_losses,
            min_trades=min_trades,
        )

        self.stdout.write(self.style.SUCCESS(f"Retrained: {len(result['trained'])} symbols"))
        for row in result["trained"][:30]:
            self.stdout.write(
                f"  {row['symbol']}: trades={row['trade_count']} losses={row['loss_count']} "
                f"rows={row['train_rows']} mode={row.get('training_mode', '')}"
            )
        if len(result["trained"]) > 30:
            self.stdout.write(f"  ... and {len(result['trained']) - 30} more")

        if result["skipped"]:
            self.stdout.write(self.style.WARNING(f"Skipped: {len(result['skipped'])}"))
            for sym, reason in list(result["skipped"].items())[:15]:
                self.stdout.write(f"  {sym}: {reason}")
