"""Source lines required by the jGrants Web-API terms (第5条)."""

from __future__ import annotations

PORTAL_URL = "https://www.jgrants-portal.go.jp/"
OPEN_API_URL = "https://www.jgrants-portal.go.jp/open-api"
API_TERMS_PDF = "https://fs2.jgrants-portal.go.jp/API%E5%88%A9%E7%94%A8%E8%A6%8F%E7%B4%84.pdf"
API_OVERVIEW_PDF = "https://fs2.jgrants-portal.go.jp/API%E5%88%A9%E7%94%A8%E6%A6%82%E8%A6%81.pdf"
API_DOCS_URL = "https://developers.digital.go.jp/documents/jgrants/api/"

# 第5条1項一 記載例（規約 PDF の文言どおり、J とグランツの間に半角スペース）
CREDIT = "出典：J グランツ"

# 第5条1項二 — 編集・加工して提供する場合（週次の差分通知など）
PROCESSED_NOTE = (
    "このコンテンツは、政府公式の補助金申請システム jGrants の Web-API 機能を利用して"
    "取得した情報をもとに本プラグイン（jp-subsidies / Hermes Agent）にて作成されたものです。"
    "コンテンツの内容は日本国政府及び自治体によって保証されたものではありません。"
)


def source_block() -> dict:
    """第5条1項一 citation plus 第5条1項二 processed_note (tool JSON is curated output)."""
    return {
        "citation": CREDIT,
        "portal": PORTAL_URL,
        "api_terms": API_TERMS_PDF,
        "api_docs": API_DOCS_URL,
        "processed_note": PROCESSED_NOTE,
    }
