"""
DGEF Nationalité Watcher
Monitors the "Demande d'accès à la Nationalité Française" tab for changes
and sends an email alert with a screenshot when something changes.
"""

import os
import time
import hashlib
import smtplib
import logging
import json
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# ---------------------------------------------------------------------------
# Configuration — override via environment variables (see .env.example)
# ---------------------------------------------------------------------------
CONFIG = {
    "DGEF_EMAIL": os.getenv("DGEF_EMAIL", ""),
    "DGEF_PASSWORD": os.getenv("DGEF_PASSWORD", ""),
    "NOTIFY_EMAIL_FROM": os.getenv("NOTIFY_EMAIL_FROM", ""),
    "NOTIFY_EMAIL_TO": os.getenv("NOTIFY_EMAIL_TO", ""),
    # Gmail App Password recommended (see README)
    "SMTP_HOST": os.getenv("SMTP_HOST", "smtp.gmail.com"),
    "SMTP_PORT": int(os.getenv("SMTP_PORT", "587")),
    "SMTP_USER": os.getenv("SMTP_USER", ""),
    "SMTP_PASSWORD": os.getenv("SMTP_PASSWORD", ""),
    # Paths
    "STATE_FILE": os.getenv("STATE_FILE", "state.json"),
    "SCREENSHOT_DIR": os.getenv("SCREENSHOT_DIR", "screenshots"),
    # Timing
    "PAGE_LOAD_TIMEOUT": int(os.getenv("PAGE_LOAD_TIMEOUT", "30")),
    "WAIT_AFTER_LOGIN": int(os.getenv("WAIT_AFTER_LOGIN", "5")),
    # Alert if no change seen for this many days (0 = disabled)
    "NO_CHANGE_ALERT_DAYS": int(os.getenv("NO_CHANGE_ALERT_DAYS", "15")),
}


# DGEF URLs — adjust if the portal changes
DGEF_LOGIN_URL = "https://sso.anef.dgef.interieur.gouv.fr/auth/realms/anef-usagers/protocol/openid-connect/auth?client_id=anef-usagers&theme=portail-anef&redirect_uri=https%3A%2F%2Fadministration-etrangers-en-france.interieur.gouv.fr%2Fparticuliers%2F%23&response_mode=fragment&response_type=code&scope=openid"
DGEF_DASHBOARD_URL = "https://administration-etrangers-en-france.interieur.gouv.fr/particuliers/#/espace-personnel/mon-compte"

# CSS / XPath selectors — inspect the live page and update these if needed
SELECTORS = {
    # Login form
    "email_field": (By.ID, "username-1757"),
    "password_field": (By.ID, "password-1758-input"),
    "login_button": (By.XPATH, "//button[@type='submit']"),
    # Tab: "Nationalité" — the tab that does NOT change the URL
    # Try text match first; fall back to a more specific selector if needed
    "nationalite_tab": (By.XPATH, "//span[contains(@class,'ui-tabview-title') and contains(text(),'Nationalit')]"),
    # The content block to watch for changes inside the tab
    # A broad selector that captures the whole tab panel content
    "content_block": (By.XPATH, "//main | //div[contains(@class,'tab-content')] | //div[contains(@class,'demande')]"),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("watcher.log"),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------

def build_driver() -> webdriver.Chrome:
    """Return a headless Chrome driver."""
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--lang=fr-FR")
    # Suppress "Chrome is being controlled by automated software" banner
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    # webdriver-manager will auto-download the right ChromeDriver
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
    except ImportError:
        # Fall back to system chromedriver
        service = Service()

    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(CONFIG["PAGE_LOAD_TIMEOUT"])
    return driver


def wait_for(driver, locator, timeout=20):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located(locator)
    )


def wait_for_clickable(driver, locator, timeout=20):
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable(locator)
    )


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def login(driver) -> bool:
    """Navigate to login page and sign in. Returns True on success."""
    log.info("Navigating to login page …")
    driver.get(DGEF_LOGIN_URL)
    time.sleep(3)  # let the SPA bootstrap

    try:
        email_el = wait_for_clickable(driver, SELECTORS["email_field"])
        email_el.clear()
        email_el.send_keys(CONFIG["DGEF_EMAIL"])

        pwd_el = wait_for_clickable(driver, SELECTORS["password_field"])
        pwd_el.clear()
        pwd_el.send_keys(CONFIG["DGEF_PASSWORD"])

        btn = wait_for_clickable(driver, SELECTORS["login_button"])
        btn.click()

        time.sleep(CONFIG["WAIT_AFTER_LOGIN"])
        log.info("Login submitted, waiting for dashboard …")
        return True

    except Exception as exc:
        log.error("Login failed: %s", exc)
        driver.save_screenshot("login_error.png")
        return False


def open_nationalite_tab(driver) -> bool:
    """Click the Nationalité tab. Returns True on success."""
    try:
        # First make sure we're on the right page
        driver.get(DGEF_DASHBOARD_URL)
        time.sleep(4)

        tab = wait_for_clickable(driver, SELECTORS["nationalite_tab"], timeout=15)
        tab.click()
        time.sleep(3)
        # Scroll the content into view
        driver.execute_script("window.scrollBy(0, 400);")
        time.sleep(1)
        log.info("Nationalité tab opened.")
        log.info("Nationalité tab opened.")
        return True

    except Exception as exc:
        log.error("Could not open Nationalité tab: %s", exc)
        driver.save_screenshot("tab_error.png")
        return False


def get_content_fingerprint(driver) -> tuple[str, str]:
    """
    Returns (hash, text) of the visible content inside the active tab.
    The hash is used for change detection; the text is stored for diffing.
    """
    try:
        # Try the specific content block first
        el = wait_for(driver, SELECTORS["content_block"], timeout=10)
        text = el.text.strip()
    except Exception:
        # Fall back to full page body text
        text = driver.find_element(By.TAG_NAME, "body").text.strip()

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return content_hash, text


def take_screenshot(driver, label: str) -> str:
    Path(CONFIG["SCREENSHOT_DIR"]).mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"{CONFIG['SCREENSHOT_DIR']}/{label}_{ts}.png"
    
    # Expand window to full page height before screenshotting
    total_height = driver.execute_script("return document.body.scrollHeight")
    driver.set_window_size(1400, total_height)
    time.sleep(1)
    driver.save_screenshot(path)
    
    # Reset window size
    driver.set_window_size(1400, 900)
    log.info("Screenshot saved: %s", path)
    return path


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    state_file = Path(CONFIG["STATE_FILE"])
    if state_file.exists():
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"hash": None, "text": "", "last_checked": None, "last_changed": None, "last_alerted_no_change": None}


def save_state(state: dict):
    with open(CONFIG["STATE_FILE"], "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Email notification
# ---------------------------------------------------------------------------

def send_alert(screenshot_path: str, new_text: str, alert_type: str = "change", days_since_change: int = 0):
    """
    Send an alert email. alert_type can be:
      "baseline"   — first run confirmation
      "change"     — content changed
      "no_change"  — no change for N days
    """
    ts = datetime.now().strftime("%d/%m/%Y à %H:%M")

    if alert_type == "baseline":
        subject = "🇫🇷 DGEF — Surveillance activée (état initial enregistré)"
        heading = "Surveillance activée"
        body_text = (
            f"Le watcher a démarré le <strong>{ts}</strong> et a enregistré l'état initial "
            f"de l'onglet <em>Demande d'accès à la Nationalité Française</em>.<br><br>"
            f"Vous recevrez un email dès qu'un changement sera détecté."
        )
    elif alert_type == "no_change":
        subject = f"🇫🇷 DGEF — Aucun changement depuis {days_since_change} jours"
        heading = f"Aucun changement depuis {days_since_change} jours"
        body_text = (
            f"Aucun changement n'a été détecté depuis <strong>{days_since_change} jours</strong> "
            f"sur l'onglet <em>Demande d'accès à la Nationalité Française</em>.<br><br>"
            f"Voici une capture d'écran de l'état actuel au {ts}."
        )
    else:  # "change"
        subject = "🇫🇷 DGEF — Changement détecté sur votre demande de nationalité"
        heading = "Changement détecté"
        body_text = (
            f"Un changement a été détecté le <strong>{ts}</strong> sur l'onglet "
            f"<em>Demande d'accès à la Nationalité Française</em>."
        )

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = CONFIG["NOTIFY_EMAIL_FROM"]
    msg["To"] = CONFIG["NOTIFY_EMAIL_TO"]

    html_body = f"""
    <html><body>
    <h2>{heading}</h2>
    <p>{body_text}</p>

    <h3>Capture d'écran de l'état actuel</h3>
    <img src="cid:screenshot" style="max-width:100%;border:1px solid #ccc;" />

    <h3>Extrait du contenu actuel</h3>
    <pre style="background:#f5f5f5;padding:12px;font-size:12px;white-space:pre-wrap;">{new_text[:3000]}</pre>

    <p style="color:#888;font-size:11px;">
    Message automatique envoyé par dgef-watcher.
    </p>
    </body></html>
    """

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Attach screenshot inline
    if screenshot_path and Path(screenshot_path).exists():
        with open(screenshot_path, "rb") as img_f:
            img = MIMEImage(img_f.read())
            img.add_header("Content-ID", "<screenshot>")
            img.add_header("Content-Disposition", "inline", filename="screenshot.png")
            msg.attach(img)

    try:
        with smtplib.SMTP(CONFIG["SMTP_HOST"], CONFIG["SMTP_PORT"]) as server:
            server.starttls()
            server.login(CONFIG["SMTP_USER"], CONFIG["SMTP_PASSWORD"])
            server.sendmail(
                CONFIG["NOTIFY_EMAIL_FROM"],
                CONFIG["NOTIFY_EMAIL_TO"],
                msg.as_string(),
            )
        log.info("Alert email sent to %s", CONFIG["NOTIFY_EMAIL_TO"])
    except Exception as exc:
        log.error("Failed to send email: %s", exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    log.info("=== DGEF watcher starting ===")

    state = load_state()
    driver = None

    try:
        driver = build_driver()

        if not login(driver):
            log.error("Aborting: login failed.")
            return

        if not open_nationalite_tab(driver):
            log.error("Aborting: could not reach Nationalité tab.")
            return

        current_hash, current_text = get_content_fingerprint(driver)
        now = datetime.now().isoformat()

        log.info("Content hash: %s", current_hash)

        if state["hash"] is None:
            # First run — record baseline and send confirmation email
            log.info("First run: baseline recorded. Sending confirmation email.")
            screenshot_path = take_screenshot(driver, "baseline")
            send_alert(screenshot_path, current_text, alert_type="baseline")
            state["last_changed"] = now
            state["last_alerted_no_change"] = now

        elif current_hash != state["hash"]:
            log.info("CHANGE DETECTED — sending alert.")
            screenshot_path = take_screenshot(driver, "change")
            send_alert(screenshot_path, current_text, alert_type="change")
            state["last_changed"] = now
            state["last_alerted_no_change"] = now

        else:
            log.info("No change detected.")
            screenshot_path = None

            # Check if we should send a "no change" reminder
            no_change_days = CONFIG["NO_CHANGE_ALERT_DAYS"]
            if no_change_days > 0 and state.get("last_changed"):
                from datetime import timezone
                last_changed_dt = datetime.fromisoformat(state["last_changed"])
                last_alerted_dt = datetime.fromisoformat(state["last_alerted_no_change"]) if state.get("last_alerted_no_change") else last_changed_dt
                now_dt = datetime.now()
                days_since_change = (now_dt - last_changed_dt).days
                days_since_last_alert = (now_dt - last_alerted_dt).days

                if days_since_change >= no_change_days and days_since_last_alert >= no_change_days:
                    log.info("No change for %d days — sending reminder.", days_since_change)
                    screenshot_path = take_screenshot(driver, "no_change")
                    send_alert(screenshot_path, current_text, alert_type="no_change", days_since_change=days_since_change)
                    state["last_alerted_no_change"] = now

        # Update state
        state["hash"] = current_hash
        state["text"] = current_text
        state["last_checked"] = now
        save_state(state)

    except Exception as exc:
        log.exception("Unexpected error: %s", exc)
        if driver:
            driver.save_screenshot("unexpected_error.png")
    finally:
        if driver:
            driver.quit()

    log.info("=== Done ===")


if __name__ == "__main__":
    run()