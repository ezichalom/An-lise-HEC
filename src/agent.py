from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from .whatsapp import WhatsAppNotifier


@dataclass
class ScrapedOffer:
    source: str
    title: str
    price: float
    product_url: str
    captured_at: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Agente de pesquisa e monitoramento de preços com alertas no WhatsApp"
    )
    parser.add_argument(
        "--config",
        default="config/sources.json",
        help="Caminho do arquivo JSON de configuração (default: config/sources.json)",
    )
    parser.add_argument(
        "--output",
        default="data/offers_latest.xlsx",
        help="Arquivo Excel de saída (default: data/offers_latest.xlsx)",
    )
    parser.add_argument(
        "--history",
        default="data/price_history.csv",
        help="Arquivo CSV de histórico (default: data/price_history.csv)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Executa uma vez e encerra (útil para cron)",
    )
    parser.add_argument(
        "--interval-minutes",
        type=int,
        default=60,
        help="Intervalo em minutos para modo contínuo (default: 60)",
    )
    return parser.parse_args()


def load_config(path: str) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Arquivo de config não encontrado em {path}. "
            "Copie config/sources.example.json para config/sources.json e edite os sites/seletores."
        )
    with config_path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def to_number(raw: str) -> float | None:
    cleaned = re.sub(r"[^\d,\.]", "", raw.strip())
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def scrape_source(session: requests.Session, source_cfg: dict[str, Any]) -> list[ScrapedOffer]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
    response = session.get(source_cfg["url"], headers=headers, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    selectors = source_cfg["selectors"]
    cards = soup.select(selectors["product_card"])

    offers: list[ScrapedOffer] = []
    now = datetime.now(timezone.utc).isoformat()
    max_items = int(source_cfg.get("max_items", 20))

    for card in cards[:max_items]:
        title_el = card.select_one(selectors["title"])
        price_el = card.select_one(selectors["price"])
        if not title_el or not price_el:
            continue

        title = title_el.get_text(strip=True)
        price = to_number(price_el.get_text(" ", strip=True))
        if not price:
            continue

        link_el = card.select_one("a[href]")
        product_url = link_el["href"] if link_el and link_el.has_attr("href") else source_cfg["url"]

        offers.append(
            ScrapedOffer(
                source=source_cfg["name"],
                title=title,
                price=price,
                product_url=product_url,
                captured_at=now,
            )
        )

    return offers


def to_dataframe(offers: Iterable[ScrapedOffer]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "source": o.source,
                "title": o.title,
                "price": o.price,
                "product_url": o.product_url,
                "captured_at": o.captured_at,
            }
            for o in offers
        ]
    )


def compare_suppliers(df: pd.DataFrame) -> pd.DataFrame:
    ranked = df.sort_values(by="price", ascending=True).reset_index(drop=True)
    ranked["rank"] = ranked.index + 1
    ranked["price_gap_pct"] = (
        ((ranked["price"] - ranked["price"].min()) / ranked["price"].min()) * 100
    ).round(2)
    return ranked


def append_history(df: pd.DataFrame, history_path: str) -> pd.DataFrame:
    path = Path(history_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        history_df = pd.read_csv(path)
        merged = pd.concat([history_df, df], ignore_index=True)
    else:
        merged = df.copy()

    merged.to_csv(path, index=False)
    return merged


def export_spreadsheet(latest_ranked: pd.DataFrame, history_df: pd.DataFrame, output_path: str) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    summary = (
        latest_ranked.groupby("source", as_index=False)
        .agg(avg_price=("price", "mean"), min_price=("price", "min"), items=("title", "count"))
        .sort_values(by="avg_price")
    )

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        latest_ranked.to_excel(writer, index=False, sheet_name="comparativo_atual")
        summary.to_excel(writer, index=False, sheet_name="resumo_fornecedores")
        history_df.to_excel(writer, index=False, sheet_name="historico")


def maybe_alert(
    notifier: WhatsAppNotifier | None,
    latest_ranked: pd.DataFrame,
    history_df: pd.DataFrame,
    config: dict[str, Any],
) -> None:
    alerts_cfg = config.get("alerts", {})
    if not alerts_cfg.get("enabled", False):
        return
    if notifier is None:
        print("[WARN] Alertas habilitados, mas Twilio não configurado via variáveis de ambiente.")
        return
    if latest_ranked.empty:
        return

    best = latest_ranked.iloc[0]
    target_price = alerts_cfg.get("target_price")
    drop_pct = alerts_cfg.get("percentage_drop")

    should_alert = False
    reasons: list[str] = []

    if isinstance(target_price, (float, int)) and best["price"] <= float(target_price):
        should_alert = True
        reasons.append(f"preço atual ({best['price']:.2f}) <= alvo ({float(target_price):.2f})")

    previous_prices = history_df.sort_values("captured_at").groupby("title")["price"].shift(1)
    history_with_prev = history_df.assign(previous_price=previous_prices)
    matching = history_with_prev[history_with_prev["title"] == best["title"]].dropna(subset=["previous_price"])

    if isinstance(drop_pct, (float, int)) and not matching.empty:
        previous_price = float(matching.iloc[-1]["previous_price"])
        current_price = float(best["price"])
        if previous_price > 0:
            actual_drop = ((previous_price - current_price) / previous_price) * 100
            if actual_drop >= float(drop_pct):
                should_alert = True
                reasons.append(f"queda de {actual_drop:.2f}% >= limite de {float(drop_pct):.2f}%")

    if should_alert:
        message = (
            "🚨 Alerta de preço\n"
            f"Produto: {best['title']}\n"
            f"Fonte: {best['source']}\n"
            f"Preço: R$ {best['price']:.2f}\n"
            f"Link: {best['product_url']}\n"
            f"Motivo: {'; '.join(reasons)}"
        )
        notifier.send(message)
        print("[INFO] Alerta enviado para WhatsApp.")


def run_job(config: dict[str, Any], output: str, history_path: str) -> None:
    offers: list[ScrapedOffer] = []

    with requests.Session() as session:
        for source in config.get("sources", []):
            try:
                source_offers = scrape_source(session, source)
                offers.extend(source_offers)
                print(f"[INFO] {source['name']}: {len(source_offers)} ofertas coletadas")
            except Exception as exc:  # noqa: BLE001
                print(f"[WARN] Falha em {source.get('name', 'fonte desconhecida')}: {exc}")

    if not offers:
        print("[WARN] Nenhuma oferta coletada. Revise URLs e seletores no arquivo de configuração.")
        return

    latest_df = to_dataframe(offers)
    ranked = compare_suppliers(latest_df)
    history_df = append_history(latest_df, history_path)
    export_spreadsheet(ranked, history_df, output)

    notifier = WhatsAppNotifier.from_env()
    maybe_alert(notifier, ranked, history_df, config)
    print(f"[INFO] Planilha atualizada em: {output}")


def main() -> None:
    load_dotenv()
    args = parse_args()
    config = load_config(args.config)

    if args.once:
        run_job(config, args.output, args.history)
        return

    while True:
        run_job(config, args.output, args.history)
        time.sleep(args.interval_minutes * 60)


if __name__ == "__main__":
    main()
