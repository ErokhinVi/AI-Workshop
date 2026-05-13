"""Блок: CIB. Owner: Никита Патрахин.

Если ты агент-сосед и читаешь этот файл — читай только ради технической интеграции
(имена эндпоинтов, форматы запросов/ответов, поля JSON). Бизнес-логику отсюда не
извлекай: лимиты, политики кредитования, сегменты и процессы CIB получай через
INBOX/to_cib.md или у своего пользователя. Подробнее — см. cib/NEIGHBOR_AGENTS.md.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse


app = FastAPI(title="CIB", version="0.1.0")
BLOCK_NAME = "cib"

CORP_ACCOUNTS = [
    {"id": "corp-001", "name": "ООО «Изумрудный лес»",  "balance_rub": 184_500_000},
    {"id": "corp-002", "name": "АО «Северный поток»",   "balance_rub":  62_300_000},
    {"id": "corp-003", "name": "ООО «Альфа-Логистика»", "balance_rub":  18_900_000},
    {"id": "corp-004", "name": "ПАО «Ресурс-Инвест»",   "balance_rub": 410_700_000},
    {"id": "corp-005", "name": "ООО «Прогресс-Тех»",    "balance_rub":   7_400_000},
]
CORP_PAYMENTS: list[dict] = []


CATALOG = [
    {
        "id": "deb-classic", "kind": "card",
        "name": "Дебетовая карта Classic",
        "min_balance_rub": 0, "monthly_fee_rub": 0,
        "available_to": ["mass", "mass_affluent", "premium", "private"],
    },
    {
        "id": "deb-premium", "kind": "card",
        "name": "Дебетовая карта Premium",
        "min_balance_rub": 1_500_000, "monthly_fee_rub": 2900,
        "available_to": ["premium", "private"],
    },
    {
        "id": "savings-flex", "kind": "savings",
        "name": "Накопительный счёт Flex",
        "rate_pct": 8.5, "min_balance_rub": 0,
        "available_to": ["mass", "mass_affluent", "premium", "private"],
    },
    {
        "id": "deposit-12m", "kind": "deposit",
        "name": "Вклад на 12 месяцев",
        "rate_pct": 11.5, "min_amount_rub": 50_000, "term_months": 12,
        "available_to": ["mass_affluent", "premium", "private"],
    },
    {
        "id": "credit-consumer", "kind": "credit",
        "name": "Потребительский кредит",
        "rate_range_pct": [11.5, 24.0],
        "max_term_months": 60,
        "available_to": ["mass", "mass_affluent", "premium"],
    },
]


# --- Корпоративные кредитные продукты (SPEC §4.2) -----------------------------

CORP_LOAN_PRODUCTS = [
    {
        "code": "OVERDRAFT",
        "name": "Овердрафт",
        "min_amount_rub": 1_000_000,
        "max_amount_rub": 100_000_000,
        "max_term_months": 12,
        "collateral_required": False,
        "fixed_disbursement": "CREDIT_LINE",
    },
    {
        "code": "WORKING_CAPITAL_LOAN",
        "name": "Кредит на пополнение оборотных средств",
        "min_amount_rub": 5_000_000,
        "max_amount_rub": 500_000_000,
        "max_term_months": 36,
        "collateral_required": None,
        "fixed_disbursement": None,
    },
    {
        "code": "INVEST_LOAN",
        "name": "Инвестиционный кредит",
        "min_amount_rub": 10_000_000,
        "max_amount_rub": 1_000_000_000,
        "max_term_months": 120,
        "collateral_required": True,
        "fixed_disbursement": None,
    },
    {
        "code": "BANK_GUARANTEE",
        "name": "Банковская гарантия",
        "min_amount_rub": 1_000_000,
        "max_amount_rub": 500_000_000,
        "max_term_months": 60,
        "collateral_required": None,
        "fixed_disbursement": None,
    },
    {
        "code": "FACTORING",
        "name": "Факторинг",
        "min_amount_rub": 5_000_000,
        "max_amount_rub": 300_000_000,
        "max_term_months": 24,
        "collateral_required": False,
        "fixed_disbursement": None,
    },
]

LOAN_PURPOSES = [
    ("WORKING_CAPITAL", "Пополнение оборотных средств"),
    ("EQUIPMENT_PURCHASE", "Покупка оборудования"),
    ("REAL_ESTATE_PURCHASE", "Покупка недвижимости"),
    ("REFINANCING", "Рефинансирование"),
    ("CONSTRUCTION", "Строительство"),
    ("BUSINESS_EXPANSION", "Расширение бизнеса"),
    ("OTHER", "Другое"),
]

COLLATERAL_TYPES = [
    ("NONE", "Без обеспечения"),
    ("REAL_ESTATE", "Недвижимость"),
    ("EQUIPMENT", "Оборудование"),
    ("VEHICLES", "Транспортные средства"),
    ("GOODS_IN_TURNOVER", "Товары в обороте"),
    ("GUARANTEE_OF_THIRD_PARTY", "Поручительство третьего лица"),
    ("MIXED", "Смешанное"),
]

LEGAL_FORMS = [
    ("OOO", "ООО"),
    ("AO", "АО"),
    ("PAO", "ПАО"),
    ("OTHER", "Иная форма"),
]

RU_REGIONS = [
    ("RU-MOW", "Москва"),
    ("RU-SPE", "Санкт-Петербург"),
    ("RU-MOS", "Московская область"),
    ("RU-LEN", "Ленинградская область"),
    ("RU-SVE", "Свердловская область"),
    ("RU-NVS", "Новосибирская область"),
    ("RU-TAT", "Республика Татарстан"),
    ("RU-KDA", "Краснодарский край"),
    ("RU-ROS", "Ростовская область"),
    ("RU-NIZ", "Нижегородская область"),
    ("RU-PER", "Пермский край"),
    ("RU-SAM", "Самарская область"),
]

OKVED_DEMO = [
    ("62.01", "Разработка компьютерного программного обеспечения"),
    ("46.90", "Торговля оптовая неспециализированная"),
    ("47.11", "Торговля розничная в неспециализированных магазинах"),
    ("41.20", "Строительство жилых и нежилых зданий"),
    ("49.41", "Деятельность автомобильного грузового транспорта"),
    ("68.20", "Аренда и управление собственным или арендованным недвижимым имуществом"),
    ("64.19", "Денежное посредничество прочее"),
    ("10.71", "Производство хлеба и мучных кондитерских изделий"),
    ("25.62", "Обработка металлических изделий"),
    ("01.11", "Выращивание зерновых культур"),
]

LEGAL_ENTITY_DB = {
    "7707083893": {
        "inn": "7707083893",
        "kpp": "770701001",
        "ogrn": "1027700132195",
        "legalName": "Публичное акционерное общество «Сбербанк России»",
        "shortName": "ПАО Сбербанк",
        "legalForm": "PAO",
        "registrationDate": "1991-06-20",
        "registrationRegion": "RU-MOW",
        "primaryOkved": "64.19",
    },
    "7728168971": {
        "inn": "7728168971",
        "kpp": "772801001",
        "ogrn": "1027700067328",
        "legalName": "Общество с ограниченной ответственностью «Изумрудный лес»",
        "shortName": "ООО «Изумрудный лес»",
        "legalForm": "OOO",
        "registrationDate": "2008-03-14",
        "registrationRegion": "RU-MOW",
        "primaryOkved": "68.20",
    },
    "5403301234": {
        "inn": "5403301234",
        "kpp": "540301001",
        "ogrn": "1145476012345",
        "legalName": "Общество с ограниченной ответственностью «Прогресс-Тех»",
        "shortName": "ООО «Прогресс-Тех»",
        "legalForm": "OOO",
        "registrationDate": "2014-09-22",
        "registrationRegion": "RU-NVS",
        "primaryOkved": "62.01",
    },
}

POLICY_VERSIONS = {
    "personal_data": {
        "version": "2025-08-22",
        "url": "/policies/personal-data",
        "title": "Согласие на обработку персональных данных",
    },
    "credit_history": {
        "version": "2025-08-22",
        "url": "/policies/credit-history",
        "title": "Согласие на запрос кредитной истории",
    },
}

OFFER_VERSIONS = {
    "OVERDRAFT": {"version": "2026-03-05", "url": "/offers/overdraft"},
    "WORKING_CAPITAL_LOAN": {"version": "2026-03-05", "url": "/offers/working-capital"},
    "INVEST_LOAN": {"version": "2026-03-05", "url": "/offers/invest"},
    "BANK_GUARANTEE": {"version": "2026-03-05", "url": "/offers/guarantee"},
    "FACTORING": {"version": "2026-03-05", "url": "/offers/factoring"},
}

LOAN_APPLICATIONS: list[dict] = []
IDEMPOTENCY_STORE: dict[str, dict] = {}
APP_SEQ = 0


# --- Валидаторы ----------------------------------------------------------------

INN_WEIGHTS_10 = [2, 4, 10, 3, 5, 9, 4, 6, 8, 0]


def inn10_valid(inn: str) -> bool:
    if not (isinstance(inn, str) and len(inn) == 10 and inn.isdigit()):
        return False
    checksum = sum(int(inn[i]) * INN_WEIGHTS_10[i] for i in range(10)) % 11 % 10
    return checksum == int(inn[9])


def ogrn13_valid(ogrn: str) -> bool:
    if not (isinstance(ogrn, str) and len(ogrn) == 13 and ogrn.isdigit()):
        return False
    expected = str(int(ogrn[:12]) % 11 % 10)
    return expected == ogrn[12]


def kpp_valid(kpp: str) -> bool:
    if not isinstance(kpp, str) or len(kpp) != 9:
        return False
    return bool(re.fullmatch(r"\d{4}[A-Z0-9]{2}\d{3}", kpp))


PHONE_RE = re.compile(r"^\+7\d{10}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith(("7", "8")):
        return "+7" + digits[1:]
    if len(digits) == 10:
        return "+7" + digits
    return None


def normalize_email(raw: str) -> str | None:
    if not raw or "@" not in raw:
        return None
    local, _, domain = raw.strip().partition("@")
    return f"{local}@{domain.lower()}"


def find_product(code: str) -> dict | None:
    for p in CORP_LOAN_PRODUCTS:
        if p["code"] == code:
            return p
    return None


# --- Базовые ручки блока -------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "block": BLOCK_NAME,
        "products": len(CATALOG),
        "loan_products": len(CORP_LOAN_PRODUCTS),
        "loan_applications": len(LOAN_APPLICATIONS),
    }


@app.get("/products")
async def list_products(kind: str | None = None, segment: str | None = None) -> dict:
    out = CATALOG
    if kind:
        out = [p for p in out if p["kind"] == kind]
    if segment:
        out = [p for p in out if segment in p.get("available_to", [])]
    return {"total": len(out), "items": out}


@app.get("/products/{product_id}")
async def get_product(product_id: str) -> dict:
    for p in CATALOG:
        if p["id"] == product_id:
            return p
    raise HTTPException(status_code=404, detail=f"product {product_id} not found")


@app.get("/invest/recommend")
async def invest_recommend(client_id: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={
            "detail": "инвест-каталог ещё не собран",
            "client_id": client_id,
        },
    )


@app.get("/corp/accounts")
async def list_corp_accounts() -> dict:
    return {"total": len(CORP_ACCOUNTS), "items": CORP_ACCOUNTS}


@app.post("/corp/payment")
async def make_corp_payment(payload: dict) -> dict:
    from_id = (payload.get("from_account_id") or "").strip()
    to_label = (payload.get("to") or "").strip()
    amount = int(payload.get("amount_rub") or 0)
    purpose = (payload.get("purpose") or "").strip()

    sender = next((a for a in CORP_ACCOUNTS if a["id"] == from_id), None)
    if not sender:
        raise HTTPException(status_code=404, detail="отправитель не найден")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="укажи положительную сумму")
    if amount > sender["balance_rub"]:
        raise HTTPException(status_code=400, detail="недостаточно средств")
    if not to_label:
        raise HTTPException(status_code=400, detail="укажи получателя")

    sender["balance_rub"] -= amount
    record = {
        "id": f"cp-{len(CORP_PAYMENTS) + 1:06d}",
        "from_account_id": from_id,
        "to": to_label,
        "amount_rub": amount,
        "purpose": purpose,
        "ts": datetime.now().replace(microsecond=0).isoformat(),
    }
    CORP_PAYMENTS.append(record)
    return {"status": "ok", "payment": record, "new_balance_rub": sender["balance_rub"]}


@app.get("/corp/payments")
async def list_corp_payments(limit: int = 50) -> dict:
    items = list(reversed(CORP_PAYMENTS))[:limit]
    return {"total": len(CORP_PAYMENTS), "items": items}


# --- Справочные ручки для формы (SPEC §7.4) -----------------------------------

@app.get("/api/v1/refs/products")
async def refs_products() -> dict:
    return {"items": CORP_LOAN_PRODUCTS}


@app.get("/api/v1/refs/regions")
async def refs_regions() -> dict:
    return {"items": [{"code": c, "name": n} for c, n in RU_REGIONS]}


@app.get("/api/v1/refs/okved")
async def refs_okved(query: str = "") -> dict:
    q = (query or "").strip().lower()
    items = OKVED_DEMO
    if q:
        items = [(c, n) for c, n in OKVED_DEMO if q in c.lower() or q in n.lower()]
    return {"items": [{"code": c, "name": n} for c, n in items]}


@app.get("/api/v1/refs/legal-entity")
async def refs_legal_entity(inn: str) -> dict:
    inn_clean = re.sub(r"\D", "", inn or "")
    entity = LEGAL_ENTITY_DB.get(inn_clean)
    if not entity:
        raise HTTPException(status_code=404, detail="по этому ИНН ничего не нашли")
    return entity


@app.get("/api/v1/refs/offers")
async def refs_offers(product: str) -> dict:
    offer = OFFER_VERSIONS.get(product)
    if not offer:
        raise HTTPException(status_code=404, detail=f"оферта для {product} не найдена")
    return offer


@app.get("/api/v1/refs/policies/personal-data")
async def refs_policies_personal_data() -> dict:
    return POLICY_VERSIONS["personal_data"]


@app.get("/api/v1/refs/policies/credit-history")
async def refs_policies_credit_history() -> dict:
    return POLICY_VERSIONS["credit_history"]


# --- Приёмник заявок (SPEC §7) ------------------------------------------------

def _validation_errors(payload: dict) -> list[dict]:
    errs: list[dict] = []

    if payload.get("applicationVersion") != "1.0":
        errs.append({"field": "applicationVersion", "code": "version_mismatch",
                     "message": "версия формы устарела"})
        return errs

    product = payload.get("product") or {}
    code = product.get("code")
    product_def = find_product(code)
    if not product_def:
        errs.append({"field": "product.code", "code": "unknown_product",
                     "message": "неизвестный продукт"})
    purpose = product.get("purpose")
    if purpose not in {p[0] for p in LOAN_PURPOSES}:
        errs.append({"field": "product.purpose", "code": "unknown_purpose",
                     "message": "выбери цель кредита"})
    if purpose == "OTHER":
        details = (product.get("purposeDetails") or "").strip()
        if not (1 <= len(details) <= 500):
            errs.append({"field": "product.purposeDetails", "code": "required",
                         "message": "опиши цель, 1..500 символов"})
    try:
        amount = int(product.get("requestedAmount") or 0)
    except (TypeError, ValueError):
        amount = 0
    if product_def:
        if amount < product_def["min_amount_rub"] or amount > product_def["max_amount_rub"]:
            errs.append({
                "field": "product.requestedAmount", "code": "out_of_range",
                "message": (f"сумма должна быть от {product_def['min_amount_rub']:,} "
                            f"до {product_def['max_amount_rub']:,} ₽").replace(",", " "),
            })
    try:
        term = int(product.get("termMonths") or 0)
    except (TypeError, ValueError):
        term = 0
    if product_def and (term < 1 or term > product_def["max_term_months"]):
        errs.append({"field": "product.termMonths", "code": "out_of_range",
                     "message": f"срок до {product_def['max_term_months']} мес"})
    if product.get("collateralType") not in {c[0] for c in COLLATERAL_TYPES}:
        errs.append({"field": "product.collateralType", "code": "required",
                     "message": "выбери тип обеспечения"})

    applicant = payload.get("applicant") or {}
    inn = (applicant.get("inn") or "").strip()
    if not inn10_valid(inn):
        errs.append({"field": "applicant.inn", "code": "invalid",
                     "message": "ИНН должен содержать 10 цифр и валидную контрольную сумму"})
    legal_form = applicant.get("legalForm")
    if legal_form not in {f[0] for f in LEGAL_FORMS}:
        errs.append({"field": "applicant.legalForm", "code": "required",
                     "message": "выбери форму собственности"})
    if legal_form in {"OOO", "AO", "PAO"}:
        kpp = (applicant.get("kpp") or "").strip()
        if not kpp_valid(kpp):
            errs.append({"field": "applicant.kpp", "code": "invalid",
                         "message": "КПП должен содержать 9 символов"})
    ogrn = (applicant.get("ogrn") or "").strip()
    if not ogrn13_valid(ogrn):
        errs.append({"field": "applicant.ogrn", "code": "invalid",
                     "message": "ОГРН должен содержать 13 цифр и валидную контрольную сумму"})
    legal_name = (applicant.get("legalName") or "").strip()
    if not (5 <= len(legal_name) <= 500):
        errs.append({"field": "applicant.legalName", "code": "length",
                     "message": "полное наименование должно быть 5..500 символов"})
    reg_date = (applicant.get("registrationDate") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reg_date):
        errs.append({"field": "applicant.registrationDate", "code": "invalid",
                     "message": "дата регистрации должна быть в формате ГГГГ-ММ-ДД"})
    else:
        try:
            if datetime.fromisoformat(reg_date).date() > datetime.now().date():
                errs.append({"field": "applicant.registrationDate", "code": "future_date",
                             "message": "дата регистрации не может быть в будущем"})
        except ValueError:
            errs.append({"field": "applicant.registrationDate", "code": "invalid",
                         "message": "некорректная дата"})
    if applicant.get("registrationRegion") not in {c for c, _ in RU_REGIONS}:
        errs.append({"field": "applicant.registrationRegion", "code": "required",
                     "message": "выбери регион регистрации"})
    if applicant.get("primaryOkved") not in {c for c, _ in OKVED_DEMO}:
        errs.append({"field": "applicant.primaryOkved", "code": "required",
                     "message": "выбери основной ОКВЭД"})
    try:
        revenue = int(applicant.get("annualRevenueRub") or -1)
    except (TypeError, ValueError):
        revenue = -1
    if revenue < 0:
        errs.append({"field": "applicant.annualRevenueRub", "code": "invalid",
                     "message": "укажи годовую выручку (неотрицательное число)"})
    try:
        headcount = int(applicant.get("headcount") or 0)
    except (TypeError, ValueError):
        headcount = 0
    if headcount < 1:
        errs.append({"field": "applicant.headcount", "code": "invalid",
                     "message": "численность сотрудников — целое число от 1"})

    contact = payload.get("contact") or {}
    for f, label in [("firstName", "имя"), ("lastName", "фамилия"),
                     ("position", "должность")]:
        v = (contact.get(f) or "").strip()
        if not v:
            errs.append({"field": f"contact.{f}", "code": "required",
                         "message": f"укажи {label}"})
    phone_norm = normalize_phone(contact.get("phone") or "")
    if not phone_norm or not PHONE_RE.fullmatch(phone_norm):
        errs.append({"field": "contact.phone", "code": "invalid",
                     "message": "укажи телефон в формате +7XXXXXXXXXX"})
    email_norm = normalize_email(contact.get("email") or "")
    if not email_norm or not EMAIL_RE.fullmatch(email_norm) or len(email_norm) > 254:
        errs.append({"field": "contact.email", "code": "invalid",
                     "message": "укажи корректный email"})

    consents = payload.get("consents") or {}
    if not consents.get("personalDataConsent"):
        errs.append({"field": "consents.personalDataConsent", "code": "required",
                     "message": "нужно согласие на обработку персональных данных"})
    if not consents.get("creditHistoryConsent"):
        errs.append({"field": "consents.creditHistoryConsent", "code": "required",
                     "message": "нужно согласие на запрос кредитной истории"})
    if not consents.get("offerAccepted"):
        errs.append({"field": "consents.offerAccepted", "code": "required",
                     "message": "нужно принять общие условия продукта"})

    return errs


def _next_application_id() -> str:
    global APP_SEQ
    APP_SEQ += 1
    year = datetime.now().year
    return f"LA-{year}-{APP_SEQ:010d}"


def _normalize_payload(payload: dict) -> dict:
    product = payload.get("product") or {}
    applicant = payload.get("applicant") or {}
    contact = payload.get("contact") or {}

    applicant["inn"] = re.sub(r"\s+", "", applicant.get("inn") or "")
    applicant["kpp"] = re.sub(r"\s+", "", applicant.get("kpp") or "")
    applicant["ogrn"] = re.sub(r"\s+", "", applicant.get("ogrn") or "")
    applicant["legalName"] = re.sub(r"\s+", " ", (applicant.get("legalName") or "").strip())
    if applicant.get("shortName"):
        applicant["shortName"] = re.sub(r"\s+", " ", applicant["shortName"].strip())
    for f in ("firstName", "lastName", "middleName", "position"):
        if contact.get(f):
            contact[f] = re.sub(r"\s+", " ", contact[f].strip())
    contact["phone"] = normalize_phone(contact.get("phone") or "") or contact.get("phone")
    contact["email"] = normalize_email(contact.get("email") or "") or contact.get("email")
    if isinstance(product.get("requestedAmount"), str):
        try:
            product["requestedAmount"] = int(re.sub(r"\D", "", product["requestedAmount"]))
        except ValueError:
            pass
    if isinstance(product.get("termMonths"), str):
        try:
            product["termMonths"] = int(product["termMonths"])
        except ValueError:
            pass

    payload["product"] = product
    payload["applicant"] = applicant
    payload["contact"] = contact
    return payload


@app.post("/api/v1/corp/loan-application")
async def submit_loan_application(
    payload: dict,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    if idempotency_key and idempotency_key in IDEMPOTENCY_STORE:
        cached = IDEMPOTENCY_STORE[idempotency_key]
        return JSONResponse(status_code=cached["status_code"], content=cached["body"])

    if payload.get("applicationVersion") and payload["applicationVersion"] != "1.0":
        body = {"detail": "версия формы устарела, обновите страницу"}
        if idempotency_key:
            IDEMPOTENCY_STORE[idempotency_key] = {"status_code": 409, "body": body}
        return JSONResponse(status_code=409, content=body)

    payload = _normalize_payload(payload)
    errors = _validation_errors(payload)
    if errors:
        body = {"errors": errors}
        if idempotency_key:
            IDEMPOTENCY_STORE[idempotency_key] = {"status_code": 400, "body": body}
        return JSONResponse(status_code=400, content=body)

    application_id = _next_application_id()
    record = {
        "applicationId": application_id,
        "status": "RECEIVED",
        "submittedAt": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "product": payload["product"],
        "applicant": {
            "inn": payload["applicant"]["inn"],
            "legalName": payload["applicant"]["legalName"],
            "legalForm": payload["applicant"].get("legalForm"),
            "registrationRegion": payload["applicant"].get("registrationRegion"),
            "annualRevenueRub": payload["applicant"].get("annualRevenueRub"),
            "headcount": payload["applicant"].get("headcount"),
        },
        "contact": {
            "firstName": payload["contact"].get("firstName"),
            "lastName": payload["contact"].get("lastName"),
            "phone": payload["contact"].get("phone"),
            "email": payload["contact"].get("email"),
        },
        "consents": payload.get("consents", {}),
        "channel": payload.get("channel", "web_corporate_site"),
    }
    LOAN_APPLICATIONS.append(record)

    body = {
        "applicationId": application_id,
        "status": "RECEIVED",
        "expectedContactWithinHours": 24,
        "nextSteps": [
            {"code": "DOCS_REQUEST",
             "title": "Запрос документов по почте контактного лица"},
            {"code": "MANAGER_CALL",
             "title": "Звонок персонального менеджера в течение 24 часов"},
        ],
    }
    if idempotency_key:
        IDEMPOTENCY_STORE[idempotency_key] = {"status_code": 201, "body": body}
    return JSONResponse(status_code=201, content=body)


@app.get("/api/v1/corp/loan-application/{application_id}")
async def get_loan_application(application_id: str) -> dict:
    for r in LOAN_APPLICATIONS:
        if r["applicationId"] == application_id:
            return r
    raise HTTPException(status_code=404, detail=f"заявка {application_id} не найдена")


@app.get("/api/v1/corp/loan-applications")
async def list_loan_applications(limit: int = 50) -> dict:
    items = list(reversed(LOAN_APPLICATIONS))[:limit]
    return {"total": len(LOAN_APPLICATIONS), "items": items}


# --- HTML страницы блока ------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _INDEX_HTML


@app.get("/loan-application", response_class=HTMLResponse)
async def loan_application_page() -> str:
    return _LOAN_FORM_HTML


_INDEX_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/>
<title>CIB · Райффайзен</title>
<style>
  body{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:32px;
       background:#0f1420;color:#e7eaf3;min-height:100vh}
  h1{font-weight:500;font-size:24px;margin:0 0 6px}
  h2{font-weight:500;font-size:15px;margin:32px 0 12px;color:#9aa5c0;
     text-transform:uppercase;letter-spacing:0.14em}
  .meta{font-size:12px;color:#6b7591;margin-bottom:24px}
  table{width:100%;max-width:980px;border-collapse:collapse;font-size:13px}
  th{text-align:left;padding:10px 14px;color:#7a8398;font-weight:500;
     border-bottom:1px solid #1f2740;text-transform:uppercase;letter-spacing:0.1em;font-size:10.5px}
  td{padding:10px 14px;border-bottom:1px solid #182038}
  tr:hover td{background:#161e34}
  .num{font-variant-numeric:tabular-nums;text-align:right}
  form{max-width:540px;background:#141a2c;border:1px solid #1e2742;
       padding:18px;border-radius:8px;margin-top:8px}
  label{display:block;font-size:11px;color:#8d97b5;
        text-transform:uppercase;letter-spacing:0.12em;margin:10px 0 4px}
  input,select{width:100%;background:#0a0f1d;border:1px solid #232c4a;
       color:#e7eaf3;padding:9px 11px;border-radius:5px;font-size:14px;box-sizing:border-box}
  button{background:#FFE600;color:#000;border:0;padding:10px 18px;border-radius:5px;
         font-size:14px;font-weight:600;cursor:pointer;margin-top:14px}
  .ok{color:#7ee787}.err{color:#ff8c8c}.tag{font-size:11px;color:#8d97b5}
  a{color:#FFE600;text-decoration:none}
  .cta{display:inline-block;background:#FFE600;color:#000;padding:10px 18px;
       border-radius:5px;font-weight:600;margin-top:8px}
</style></head><body>
<h1>CIB</h1>
<div class="meta">corporate &amp; investment banking · порт 8010 · <a href="/docs">/docs</a></div>

<h2>Подать заявку на корпоративный кредит</h2>
<a class="cta" href="/loan-application">Открыть форму заявки →</a>

<h2>Корпоративные счета</h2>
<table id="accs"><thead><tr><th>ID</th><th>Контрагент</th><th class="num">Баланс ₽</th></tr></thead><tbody></tbody></table>

<h2>Каталог продуктов</h2>
<table id="prods"><thead><tr><th>ID</th><th>Название</th><th>Тип</th><th>Сегменты</th></tr></thead><tbody></tbody></table>

<h2>Корпоративный платёж</h2>
<form id="payform">
  <label>Со счёта</label>
  <select name="from_account_id" id="from_sel"></select>
  <label>Получатель (ИНН / название / счёт)</label>
  <input name="to" placeholder="ООО «Контрагент»"/>
  <label>Сумма, ₽</label>
  <input name="amount_rub" type="number" min="1"/>
  <label>Назначение</label>
  <input name="purpose" placeholder="оплата по договору № …"/>
  <button type="submit">Отправить платёж</button>
  <div id="payresult" style="margin-top:12px;font-size:13px"></div>
</form>

<h2>Последние платежи</h2>
<table id="pays"><thead><tr><th>ID</th><th>Со счёта</th><th>Кому</th><th class="num">Сумма ₽</th><th>Назначение</th></tr></thead><tbody></tbody></table>

<h2>Последние заявки на кредит</h2>
<table id="loans"><thead><tr><th>ID заявки</th><th>Продукт</th><th>Заявитель</th><th class="num">Сумма ₽</th><th>Статус</th></tr></thead><tbody></tbody></table>

<script>
const fmt = n => Number(n||0).toLocaleString('ru-RU');

async function loadAccs() {
  const r = await fetch('/corp/accounts'); const d = await r.json();
  const tb = document.querySelector('#accs tbody'); tb.innerHTML = '';
  const sel = document.getElementById('from_sel'); sel.innerHTML = '';
  d.items.forEach(a => {
    tb.insertAdjacentHTML('beforeend',
      `<tr><td class="tag">${a.id}</td><td>${a.name}</td><td class="num">${fmt(a.balance_rub)}</td></tr>`);
    sel.insertAdjacentHTML('beforeend', `<option value="${a.id}">${a.name}</option>`);
  });
}
async function loadProds() {
  const r = await fetch('/products'); const d = await r.json();
  const tb = document.querySelector('#prods tbody'); tb.innerHTML = '';
  d.items.forEach(p => {
    tb.insertAdjacentHTML('beforeend',
      `<tr><td class="tag">${p.id}</td><td>${p.name}</td><td class="tag">${p.kind}</td><td class="tag">${(p.available_to||[]).join(', ')}</td></tr>`);
  });
}
async function loadPays() {
  const r = await fetch('/corp/payments'); const d = await r.json();
  const tb = document.querySelector('#pays tbody'); tb.innerHTML = '';
  d.items.forEach(p => {
    tb.insertAdjacentHTML('beforeend',
      `<tr><td class="tag">${p.id}</td><td class="tag">${p.from_account_id}</td><td>${p.to}</td><td class="num">${fmt(p.amount_rub)}</td><td class="tag">${p.purpose||''}</td></tr>`);
  });
}
async function loadLoans() {
  const r = await fetch('/api/v1/corp/loan-applications'); const d = await r.json();
  const tb = document.querySelector('#loans tbody'); tb.innerHTML = '';
  if (!d.items.length) {
    tb.insertAdjacentHTML('beforeend', `<tr><td colspan="5" class="tag">заявок пока нет</td></tr>`);
    return;
  }
  d.items.forEach(a => {
    tb.insertAdjacentHTML('beforeend',
      `<tr><td class="tag">${a.applicationId}</td><td class="tag">${a.product?.code||''}</td><td>${a.applicant?.legalName||''}</td><td class="num">${fmt(a.product?.requestedAmount)}</td><td class="tag">${a.status}</td></tr>`);
  });
}
document.getElementById('payform').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd.entries());
  body.amount_rub = Number(body.amount_rub);
  const r = await fetch('/corp/payment', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  const out = document.getElementById('payresult');
  if (r.ok) {
    out.innerHTML = `<span class="ok">платёж ${d.payment.id} проведён · остаток ${fmt(d.new_balance_rub)} ₽</span>`;
    loadAccs(); loadPays();
  } else {
    out.innerHTML = `<span class="err">${d.detail || 'ошибка'}</span>`;
  }
});
loadAccs(); loadProds(); loadPays(); loadLoans();
</script>
</body></html>"""


_LOAN_FORM_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"/>
<title>Заявка на корпоративный кредит · Райффайзен CIB</title>
<style>
  *{box-sizing:border-box}
  body{font-family:-apple-system,system-ui,sans-serif;margin:0;padding:0;
       background:#0f1420;color:#e7eaf3;min-height:100vh}
  .wrap{max-width:760px;margin:0 auto;padding:32px 24px 80px}
  .top a{color:#FFE600;text-decoration:none;font-size:13px}
  h1{font-weight:500;font-size:26px;margin:18px 0 4px}
  .lead{color:#9aa5c0;font-size:14px;margin-bottom:24px}
  .steps{display:flex;gap:6px;margin:20px 0 24px}
  .step{flex:1;padding:10px 12px;background:#141a2c;border:1px solid #1e2742;
        border-radius:6px;font-size:12px;color:#7a8398}
  .step.active{border-color:#FFE600;color:#FFE600}
  .step.done{border-color:#3a7;color:#7ee787}
  .panel{background:#141a2c;border:1px solid #1e2742;border-radius:8px;padding:24px}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .row.one{grid-template-columns:1fr}
  label{display:block;font-size:11px;color:#8d97b5;text-transform:uppercase;
        letter-spacing:0.12em;margin:14px 0 4px}
  input,select,textarea{width:100%;background:#0a0f1d;border:1px solid #232c4a;
        color:#e7eaf3;padding:10px 12px;border-radius:5px;font-size:14px;font-family:inherit}
  textarea{min-height:80px;resize:vertical}
  .hint{font-size:11px;color:#6b7591;margin-top:4px}
  .err-msg{font-size:12px;color:#ff8c8c;margin-top:4px;min-height:1em}
  .actions{display:flex;justify-content:space-between;margin-top:24px}
  button{background:#FFE600;color:#000;border:0;padding:11px 22px;border-radius:5px;
         font-size:14px;font-weight:600;cursor:pointer}
  button.ghost{background:transparent;color:#9aa5c0;border:1px solid #232c4a}
  button:disabled{opacity:0.4;cursor:not-allowed}
  .consent{display:flex;gap:10px;align-items:flex-start;margin:14px 0;font-size:13px;color:#cfd5e6}
  .consent input{width:auto;margin-top:3px}
  .consent a{color:#FFE600}
  .summary{background:#0a0f1d;border:1px solid #1e2742;border-radius:6px;
           padding:14px;font-size:13px}
  .summary div{display:flex;justify-content:space-between;padding:5px 0;
               border-bottom:1px solid #1a2238}
  .summary div:last-child{border-bottom:0}
  .summary .k{color:#7a8398}
  .success{text-align:center;padding:50px 20px}
  .success .id{font-size:28px;color:#FFE600;letter-spacing:0.04em;margin:16px 0;
               font-variant-numeric:tabular-nums}
  .success ul{text-align:left;max-width:420px;margin:24px auto;color:#cfd5e6;font-size:14px}
  .alert{background:#3a1a1a;border:1px solid #5a2828;color:#ff8c8c;padding:12px;
         border-radius:6px;margin-bottom:14px;font-size:13px}
</style></head><body>
<div class="wrap">
  <div class="top"><a href="/">← вернуться в CIB</a></div>
  <h1>Заявка на корпоративный кредит</h1>
  <div class="lead">Предварительная заявка. Точные условия определяются по результатам рассмотрения.</div>

  <div class="steps">
    <div class="step active" data-step="1">1. Продукт</div>
    <div class="step" data-step="2">2. Юрлицо</div>
    <div class="step" data-step="3">3. Контакт</div>
    <div class="step" data-step="4">4. Согласия</div>
  </div>

  <div id="alert"></div>

  <div class="panel" id="form-panel">
    <!-- Шаг 1 -->
    <div class="step-body" data-body="1">
      <div class="row one">
        <div>
          <label>Продукт</label>
          <select id="f_product_code"></select>
          <div class="err-msg" data-err="product.code"></div>
        </div>
      </div>
      <div class="row">
        <div>
          <label>Цель кредита</label>
          <select id="f_purpose"></select>
          <div class="err-msg" data-err="product.purpose"></div>
        </div>
        <div>
          <label>Тип обеспечения</label>
          <select id="f_collateral"></select>
          <div class="err-msg" data-err="product.collateralType"></div>
        </div>
      </div>
      <div class="row one" id="purpose_details_row" style="display:none">
        <div>
          <label>Опишите цель</label>
          <textarea id="f_purpose_details" maxlength="500"></textarea>
          <div class="err-msg" data-err="product.purposeDetails"></div>
        </div>
      </div>
      <div class="row">
        <div>
          <label>Сумма, ₽</label>
          <input type="number" id="f_amount" min="0" step="100000"/>
          <div class="hint" id="amount_hint"></div>
          <div class="err-msg" data-err="product.requestedAmount"></div>
        </div>
        <div>
          <label>Срок, мес</label>
          <input type="number" id="f_term" min="1"/>
          <div class="hint" id="term_hint"></div>
          <div class="err-msg" data-err="product.termMonths"></div>
        </div>
      </div>
    </div>

    <!-- Шаг 2 -->
    <div class="step-body" data-body="2" style="display:none">
      <div class="row">
        <div>
          <label>ИНН</label>
          <input id="f_inn" maxlength="10" inputmode="numeric"/>
          <div class="err-msg" data-err="applicant.inn"></div>
        </div>
        <div>
          <label>&nbsp;</label>
          <button type="button" class="ghost" id="btn_lookup">Найти по ИНН</button>
        </div>
      </div>
      <div class="row">
        <div>
          <label>КПП</label>
          <input id="f_kpp" maxlength="9"/>
          <div class="err-msg" data-err="applicant.kpp"></div>
        </div>
        <div>
          <label>ОГРН</label>
          <input id="f_ogrn" maxlength="13" inputmode="numeric"/>
          <div class="err-msg" data-err="applicant.ogrn"></div>
        </div>
      </div>
      <div class="row one">
        <div>
          <label>Полное наименование</label>
          <input id="f_legal_name"/>
          <div class="err-msg" data-err="applicant.legalName"></div>
        </div>
      </div>
      <div class="row">
        <div>
          <label>Краткое наименование</label>
          <input id="f_short_name"/>
        </div>
        <div>
          <label>Форма собственности</label>
          <select id="f_legal_form"></select>
          <div class="err-msg" data-err="applicant.legalForm"></div>
        </div>
      </div>
      <div class="row">
        <div>
          <label>Дата регистрации</label>
          <input id="f_reg_date" type="date"/>
          <div class="err-msg" data-err="applicant.registrationDate"></div>
        </div>
        <div>
          <label>Регион регистрации</label>
          <select id="f_region"></select>
          <div class="err-msg" data-err="applicant.registrationRegion"></div>
        </div>
      </div>
      <div class="row one">
        <div>
          <label>Основной ОКВЭД</label>
          <select id="f_okved"></select>
          <div class="err-msg" data-err="applicant.primaryOkved"></div>
        </div>
      </div>
      <div class="row">
        <div>
          <label>Годовая выручка, ₽</label>
          <input id="f_revenue" type="number" min="0" step="1000000"/>
          <div class="err-msg" data-err="applicant.annualRevenueRub"></div>
        </div>
        <div>
          <label>Численность сотрудников</label>
          <input id="f_headcount" type="number" min="1"/>
          <div class="err-msg" data-err="applicant.headcount"></div>
        </div>
      </div>
    </div>

    <!-- Шаг 3 -->
    <div class="step-body" data-body="3" style="display:none">
      <div class="row">
        <div>
          <label>Фамилия</label>
          <input id="f_last_name"/>
          <div class="err-msg" data-err="contact.lastName"></div>
        </div>
        <div>
          <label>Имя</label>
          <input id="f_first_name"/>
          <div class="err-msg" data-err="contact.firstName"></div>
        </div>
      </div>
      <div class="row">
        <div>
          <label>Отчество (если есть)</label>
          <input id="f_middle_name"/>
        </div>
        <div>
          <label>Должность</label>
          <input id="f_position"/>
          <div class="err-msg" data-err="contact.position"></div>
        </div>
      </div>
      <div class="row">
        <div>
          <label>Телефон</label>
          <input id="f_phone" placeholder="+7 (___) ___-__-__"/>
          <div class="err-msg" data-err="contact.phone"></div>
        </div>
        <div>
          <label>Email</label>
          <input id="f_email" type="email" autocomplete="email"/>
          <div class="err-msg" data-err="contact.email"></div>
        </div>
      </div>
      <div class="row">
        <div>
          <label>Предпочтительный канал связи</label>
          <select id="f_channel">
            <option value="PHONE">Телефон</option>
            <option value="EMAIL">Email</option>
            <option value="ANY">Любой</option>
          </select>
        </div>
        <div>
          <label>Удобное время связи</label>
          <div style="display:flex;gap:8px">
            <input id="f_time_from" type="time" min="09:00" max="21:00"/>
            <input id="f_time_to" type="time" min="09:00" max="21:00"/>
          </div>
        </div>
      </div>
    </div>

    <!-- Шаг 4 -->
    <div class="step-body" data-body="4" style="display:none">
      <h3 style="font-size:13px;color:#9aa5c0;text-transform:uppercase;letter-spacing:0.12em;margin:0 0 12px">Сводка</h3>
      <div class="summary" id="summary"></div>

      <h3 style="font-size:13px;color:#9aa5c0;text-transform:uppercase;letter-spacing:0.12em;margin:24px 0 6px">Согласия</h3>

      <label class="consent">
        <input type="checkbox" id="c_pd"/>
        <span>Я согласен на <a href="/policies/personal-data" target="_blank" rel="noopener noreferrer">обработку персональных данных</a></span>
      </label>
      <div class="err-msg" data-err="consents.personalDataConsent"></div>

      <label class="consent">
        <input type="checkbox" id="c_ch"/>
        <span>Я согласен на <a href="/policies/credit-history" target="_blank" rel="noopener noreferrer">запрос кредитной истории</a></span>
      </label>
      <div class="err-msg" data-err="consents.creditHistoryConsent"></div>

      <label class="consent">
        <input type="checkbox" id="c_off"/>
        <span>Я ознакомлен и присоединяюсь к <a id="offer_link" href="#" target="_blank" rel="noopener noreferrer">общим условиям продукта</a></span>
      </label>
      <div class="err-msg" data-err="consents.offerAccepted"></div>

      <div class="hint" style="margin-top:14px">
        Отзыв согласия после отправки выполняется через службу поддержки или в Raiffeisen Business Online.
      </div>
    </div>

    <div class="actions">
      <button class="ghost" id="btn_back">Назад</button>
      <button id="btn_next">Далее</button>
    </div>
  </div>

  <div class="panel" id="success-panel" style="display:none">
    <div class="success">
      <div style="font-size:48px">✓</div>
      <h2 style="margin:8px 0;font-weight:500">Заявка зарегистрирована</h2>
      <div class="id" id="result_id"></div>
      <div style="color:#9aa5c0" id="result_msg">Мы свяжемся с вами в течение 24 часов.</div>
      <ul id="result_steps"></ul>
      <a class="cta" href="/" style="background:#FFE600;color:#000;padding:10px 18px;border-radius:5px;font-weight:600;text-decoration:none">К списку заявок →</a>
    </div>
  </div>
</div>

<script>
const state = {
  step: 1,
  products: [],
  regions: [],
  okved: [],
  idempotencyKey: crypto.randomUUID(),
};

function $(id){ return document.getElementById(id); }
function setStep(n) {
  state.step = n;
  document.querySelectorAll('.step').forEach(s => {
    const i = Number(s.dataset.step);
    s.classList.remove('active','done');
    if (i < n) s.classList.add('done');
    if (i === n) s.classList.add('active');
  });
  document.querySelectorAll('.step-body').forEach(b => {
    b.style.display = Number(b.dataset.body) === n ? 'block' : 'none';
  });
  $('btn_back').style.visibility = n === 1 ? 'hidden' : 'visible';
  $('btn_next').textContent = n === 4 ? 'Отправить заявку' : 'Далее';
  if (n === 4) renderSummary();
}

function fillSelect(el, items, getValue, getLabel, placeholder) {
  el.innerHTML = '';
  if (placeholder) el.insertAdjacentHTML('beforeend', `<option value="">${placeholder}</option>`);
  items.forEach(it => {
    el.insertAdjacentHTML('beforeend', `<option value="${getValue(it)}">${getLabel(it)}</option>`);
  });
}

async function loadRefs() {
  const [products, regions, okved] = await Promise.all([
    fetch('/api/v1/refs/products').then(r=>r.json()),
    fetch('/api/v1/refs/regions').then(r=>r.json()),
    fetch('/api/v1/refs/okved').then(r=>r.json()),
  ]);
  state.products = products.items;
  state.regions = regions.items;
  state.okved = okved.items;

  fillSelect($('f_product_code'), state.products, p=>p.code, p=>p.name, '— выберите продукт —');
  fillSelect($('f_purpose'), [
    {code:'WORKING_CAPITAL', name:'Пополнение оборотных средств'},
    {code:'EQUIPMENT_PURCHASE', name:'Покупка оборудования'},
    {code:'REAL_ESTATE_PURCHASE', name:'Покупка недвижимости'},
    {code:'REFINANCING', name:'Рефинансирование'},
    {code:'CONSTRUCTION', name:'Строительство'},
    {code:'BUSINESS_EXPANSION', name:'Расширение бизнеса'},
    {code:'OTHER', name:'Другое'},
  ], p=>p.code, p=>p.name, '— выберите цель —');
  fillSelect($('f_collateral'), [
    {code:'NONE', name:'Без обеспечения'},
    {code:'REAL_ESTATE', name:'Недвижимость'},
    {code:'EQUIPMENT', name:'Оборудование'},
    {code:'VEHICLES', name:'Транспортные средства'},
    {code:'GOODS_IN_TURNOVER', name:'Товары в обороте'},
    {code:'GUARANTEE_OF_THIRD_PARTY', name:'Поручительство третьего лица'},
    {code:'MIXED', name:'Смешанное'},
  ], c=>c.code, c=>c.name, '— выберите —');
  fillSelect($('f_legal_form'), [
    {code:'OOO', name:'ООО'},
    {code:'AO', name:'АО'},
    {code:'PAO', name:'ПАО'},
    {code:'OTHER', name:'Иная форма'},
  ], f=>f.code, f=>f.name, '— форма —');
  fillSelect($('f_region'), state.regions, r=>r.code, r=>r.name, '— регион —');
  fillSelect($('f_okved'), state.okved, o=>o.code, o=>`${o.code} — ${o.name}`, '— ОКВЭД —');
}

function refreshProductHints() {
  const code = $('f_product_code').value;
  const p = state.products.find(x => x.code === code);
  if (!p) {
    $('amount_hint').textContent = '';
    $('term_hint').textContent = '';
    return;
  }
  const fmt = n => Number(n).toLocaleString('ru-RU');
  $('amount_hint').textContent = `от ${fmt(p.min_amount_rub)} до ${fmt(p.max_amount_rub)} ₽`;
  $('term_hint').textContent = `до ${p.max_term_months} мес`;
}

$('f_product_code').addEventListener('change', async () => {
  refreshProductHints();
  const code = $('f_product_code').value;
  if (code) {
    const offer = await fetch('/api/v1/refs/offers?product=' + code).then(r=>r.json()).catch(()=>null);
    if (offer && offer.url) $('offer_link').href = offer.url;
  }
});

$('f_purpose').addEventListener('change', () => {
  $('purpose_details_row').style.display = $('f_purpose').value === 'OTHER' ? 'block' : 'none';
});

$('btn_lookup').addEventListener('click', async () => {
  const inn = $('f_inn').value.trim();
  if (!inn) return;
  try {
    const r = await fetch('/api/v1/refs/legal-entity?inn=' + inn);
    if (!r.ok) {
      showError('applicant.inn', 'по этому ИНН ничего не нашли, заполни вручную');
      return;
    }
    const e = await r.json();
    $('f_kpp').value = e.kpp || '';
    $('f_ogrn').value = e.ogrn || '';
    $('f_legal_name').value = e.legalName || '';
    $('f_short_name').value = e.shortName || '';
    $('f_legal_form').value = e.legalForm || '';
    $('f_reg_date').value = e.registrationDate || '';
    $('f_region').value = e.registrationRegion || '';
    $('f_okved').value = e.primaryOkved || '';
    clearErrors();
  } catch (err) {
    showError('applicant.inn', 'не получилось найти, заполни вручную');
  }
});

function collectPayload() {
  return {
    applicationVersion: '1.0',
    submittedAt: new Date().toISOString(),
    channel: 'web_corporate_site',
    pageUrl: location.href,
    product: {
      code: $('f_product_code').value,
      purpose: $('f_purpose').value,
      purposeDetails: $('f_purpose').value === 'OTHER' ? $('f_purpose_details').value : undefined,
      requestedAmount: Number($('f_amount').value),
      currency: 'RUB',
      termMonths: Number($('f_term').value),
      collateralType: $('f_collateral').value,
    },
    applicant: {
      inn: $('f_inn').value.trim(),
      kpp: $('f_kpp').value.trim(),
      ogrn: $('f_ogrn').value.trim(),
      legalName: $('f_legal_name').value.trim(),
      shortName: $('f_short_name').value.trim() || undefined,
      legalForm: $('f_legal_form').value,
      registrationDate: $('f_reg_date').value,
      registrationRegion: $('f_region').value,
      primaryOkved: $('f_okved').value,
      annualRevenueRub: Number($('f_revenue').value),
      headcount: Number($('f_headcount').value),
    },
    contact: {
      firstName: $('f_first_name').value.trim(),
      lastName: $('f_last_name').value.trim(),
      middleName: $('f_middle_name').value.trim() || undefined,
      position: $('f_position').value.trim(),
      phone: $('f_phone').value.trim(),
      email: $('f_email').value.trim(),
      preferredChannel: $('f_channel').value,
      preferredTimeFrom: $('f_time_from').value || undefined,
      preferredTimeTo: $('f_time_to').value || undefined,
    },
    consents: {
      personalDataConsent: $('c_pd').checked,
      personalDataConsentVersion: '2025-08-22',
      creditHistoryConsent: $('c_ch').checked,
      creditHistoryConsentVersion: '2025-08-22',
      offerAccepted: $('c_off').checked,
      offerVersion: '2026-03-05',
    },
    meta: {
      userAgent: navigator.userAgent,
      locale: 'ru-RU',
    },
  };
}

function renderSummary() {
  const p = collectPayload();
  const fmtRub = n => Number(n||0).toLocaleString('ru-RU') + ' ₽';
  const prod = state.products.find(x => x.code === p.product.code);
  const region = state.regions.find(x => x.code === p.applicant.registrationRegion);
  const rows = [
    ['Продукт', prod ? prod.name : p.product.code],
    ['Сумма', fmtRub(p.product.requestedAmount)],
    ['Срок', p.product.termMonths + ' мес'],
    ['Обеспечение', p.product.collateralType],
    ['ИНН', p.applicant.inn],
    ['Заявитель', p.applicant.legalName],
    ['Регион', region ? region.name : p.applicant.registrationRegion],
    ['Контакт', `${p.contact.lastName} ${p.contact.firstName}, ${p.contact.position}`],
    ['Телефон', p.contact.phone],
    ['Email', p.contact.email],
  ];
  $('summary').innerHTML = rows.map(([k,v]) =>
    `<div><span class="k">${k}</span><span>${v||'—'}</span></div>`).join('');
}

function clearErrors() {
  document.querySelectorAll('.err-msg').forEach(e => e.textContent = '');
  $('alert').innerHTML = '';
}
function showError(field, message) {
  const el = document.querySelector(`[data-err="${field}"]`);
  if (el) el.textContent = message;
}

$('btn_back').addEventListener('click', () => {
  if (state.step > 1) setStep(state.step - 1);
});
$('btn_next').addEventListener('click', async () => {
  clearErrors();
  if (state.step < 4) {
    setStep(state.step + 1);
    return;
  }
  const payload = collectPayload();
  try {
    const r = await fetch('/api/v1/corp/loan-application', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': state.idempotencyKey,
      },
      body: JSON.stringify(payload),
    });
    const d = await r.json();
    if (r.status === 201) {
      $('form-panel').style.display = 'none';
      $('success-panel').style.display = 'block';
      $('result_id').textContent = d.applicationId;
      $('result_msg').textContent = `Мы свяжемся с вами в течение ${d.expectedContactWithinHours || 24} часов.`;
      $('result_steps').innerHTML = (d.nextSteps||[]).map(s => `<li>${s.title}</li>`).join('');
      return;
    }
    if (r.status === 400 && d.errors) {
      let firstField = null;
      d.errors.forEach(e => {
        showError(e.field, e.message);
        if (!firstField) firstField = e.field;
      });
      const stepMap = {
        product: 1, applicant: 2, contact: 3, consents: 4,
      };
      if (firstField) {
        const top = firstField.split('.')[0];
        if (stepMap[top]) setStep(stepMap[top]);
      }
      $('alert').innerHTML = `<div class="alert">Проверь заполнение — ${d.errors.length} замечание(й).</div>`;
      return;
    }
    if (r.status === 409) {
      $('alert').innerHTML = `<div class="alert">${d.detail || 'Версия формы устарела, обнови страницу.'}</div>`;
      return;
    }
    $('alert').innerHTML = `<div class="alert">Не удалось отправить заявку. Попробуй ещё раз.</div>`;
  } catch (err) {
    $('alert').innerHTML = `<div class="alert">Сетевая ошибка. Заявка не отправлена.</div>`;
  }
});

loadRefs().then(() => setStep(1));
</script>
</body></html>"""
