import os
import time
import random
import logging
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
EMAIL            = os.environ.get("CR_EMAIL", "your_email@example.com")
PASSWORD         = os.environ.get("CR_PASSWORD", "your_password")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "your_bot_token")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8552808380")

DASHBOARD_URL = "https://connect.cloudresearch.com/participant/dashboard"
LOGIN_URL     = "https://account.cloudresearch.com/Account/Login?AppDestination=Connect"

# Check every 90–150 seconds (randomised)
CHECK_INTERVAL_MIN = 20
CHECK_INTERVAL_MAX = 60

# Re-login every 2–4 hours (randomised), expressed in number of checks
RELOGIN_AFTER_MIN = int((2 * 3600) / CHECK_INTERVAL_MAX)
RELOGIN_AFTER_MAX = int((4 * 3600) / CHECK_INTERVAL_MIN)

# ── QUIET HOURS (Ireland time, 24h) ──────────────────────────────────────────
# Every night: off from 00:00 to 07:00
QUIET_NIGHT_START = 0    # midnight
QUIET_NIGHT_END   = 7    # 7am

# Tuesdays: off from 05:00 to 15:00
QUIET_TUESDAY_START = 5   # 5am
QUIET_TUESDAY_END   = 15  # 3pm
# ─────────────────────────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def is_quiet_time() -> tuple[bool, str]:
    """
    Returns (True, reason) if monitoring should be paused right now.
    Uses Ireland local time (set TZ=Europe/Dublin on your server).
    """
    now = datetime.now()
    hour = now.hour
    weekday = now.weekday()  # Monday=0, Tuesday=1, ..., Sunday=6

    # Every night: 00:00–07:00
    if QUIET_NIGHT_START <= hour < QUIET_NIGHT_END:
        return True, f"quiet night hours ({QUIET_NIGHT_START}:00–{QUIET_NIGHT_END}:00)"

    # Tuesdays: 05:00–15:00
    if weekday == 1 and QUIET_TUESDAY_START <= hour < QUIET_TUESDAY_END:
        return True, f"Tuesday quiet hours ({QUIET_TUESDAY_START}:00–{QUIET_TUESDAY_END}:00)"

    return False, ""


def seconds_until_active() -> int:
    """
    How many seconds until the next active window starts.
    Checks every minute — just sleeps 60s at a time in the quiet loop.
    """
    return 60


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
        resp.raise_for_status()
        log.info("Telegram notification sent.")
    except Exception as e:
        log.error(f"Failed to send Telegram message: {e}")


def human_delay(min_s=0.5, max_s=1.5):
    time.sleep(random.uniform(min_s, max_s))


def make_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(f"user-agent={random.choice(USER_AGENTS)}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver


def login(driver: webdriver.Chrome) -> bool:
    try:
        log.info("Navigating to login page...")
        driver.get(LOGIN_URL)
        wait = WebDriverWait(driver, 20)
        human_delay(2, 4)

        log.info(f"URL: {driver.current_url} | Title: {driver.title}")

        # Dismiss cookie banner
        try:
            cookie_btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(text(), 'Accept')]")
            ))
            cookie_btn.click()
            log.info("Cookie banner dismissed.")
            human_delay(0.5, 1.5)
        except Exception:
            log.info("No cookie banner, continuing.")

        # Click Connect for Participants card
        try:
            connect_card = wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "div[data-app-destination='Connect_Participant']")
            ))
            driver.execute_script("arguments[0].click();", connect_card)
            log.info("Clicked Connect (Participant) card.")
            human_delay(1.5, 3)
        except Exception as e:
            log.error(f"Could not click Connect card: {e}")
            driver.save_screenshot("card_click_failed.png")
            return False

        # Type email
        email_field = wait.until(EC.element_to_be_clickable((By.ID, "Email")))
        driver.execute_script("arguments[0].scrollIntoView(true);", email_field)
        human_delay(0.3, 0.8)
        email_field.clear()
        for char in EMAIL:
            email_field.send_keys(char)
            time.sleep(random.uniform(0.03, 0.12))
        log.info("Email entered.")
        human_delay(0.3, 0.8)

        # Type password
        password_field = wait.until(EC.element_to_be_clickable((By.ID, "Password")))
        password_field.clear()
        for char in PASSWORD:
            password_field.send_keys(char)
            time.sleep(random.uniform(0.03, 0.12))
        log.info("Password entered.")
        human_delay(0.5, 1.2)

        # Submit
        submit = wait.until(EC.element_to_be_clickable((By.ID, "log-in-btn")))
        driver.execute_script("arguments[0].click();", submit)
        log.info("Submit clicked, waiting for redirect...")

        for i in range(25):
            time.sleep(1)
            current = driver.current_url
            log.info(f"  [{i+1}s] {current}")
            if "Account/Login" not in current and "cloudresearch.com" in current:
                log.info(f"Login successful! Landed on: {current}")
                return True

        log.error("Login failed — never redirected from login page.")
        driver.save_screenshot("login_failed.png")
        return False

    except TimeoutException as e:
        log.error(f"Login timed out: {e}")
        driver.save_screenshot("login_timeout.png")
        return False
    except Exception as e:
        log.error(f"Login error: {e}")
        driver.save_screenshot("login_error.png")
        return False


def has_no_results(driver: webdriver.Chrome) -> bool:
    """Returns True if 'No results' is visible (no projects). False = projects available!"""
    try:
        driver.get(DASHBOARD_URL)
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        human_delay(3, 5)

        log.info(f"Dashboard loaded: {driver.current_url}")

        no_results_elements = driver.find_elements(
            By.XPATH, "//*[normalize-space(text())='No results']"
        )

        if no_results_elements:
            log.info("'No results' found — no projects yet.")
            return True
        else:
            log.info("'No results' gone — projects available!")
            return False

    except Exception as e:
        log.error(f"Error checking dashboard: {e}")
        driver.save_screenshot("dashboard_error.png")
        return True


def monitor():
    log.info("Starting CloudResearch monitor...")
    send_telegram(
        "🟢 CloudResearch monitor started!\n"
        f"😴 Quiet hours: every night {QUIET_NIGHT_START}:00–{QUIET_NIGHT_END}:00, "
        f"Tuesdays {QUIET_TUESDAY_START}:00–{QUIET_TUESDAY_END}:00"
    )

    driver = None
    logged_in = False
    session_checks = 0
    relogin_after = random.randint(RELOGIN_AFTER_MIN, RELOGIN_AFTER_MAX)
    projects_alerted = False
    was_quiet = False  # track transitions to avoid re-login spam after waking

    while True:
        # ── Quiet hours check ────────────────────────────────────────────────
        quiet, reason = is_quiet_time()
        if quiet:
            if not was_quiet:
                log.info(f"Entering quiet period: {reason}. Pausing monitoring.")
                send_telegram(f"😴 Pausing monitoring ({reason}).")
                # Close browser during quiet hours to save resources
                if driver:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = None
                    logged_in = False
                was_quiet = True
            time.sleep(60)  # check every minute whether quiet hours are over
            continue

        if was_quiet:
            log.info("Quiet period ended, resuming monitoring.")
            send_telegram("⏰ Resuming monitoring.")
            was_quiet = False
        # ────────────────────────────────────────────────────────────────────

        try:
            # Login / re-login
            if driver is None or not logged_in or session_checks >= relogin_after:
                if driver:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                driver = make_driver()
                logged_in = login(driver)
                session_checks = 0
                relogin_after = random.randint(RELOGIN_AFTER_MIN, RELOGIN_AFTER_MAX)
                log.info(f"Next re-login in ~{relogin_after} checks.")

                if not logged_in:
                    send_telegram("⚠️ Login failed. Retrying in 5 minutes.")
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = None
                    time.sleep(300)
                    continue

            # Check dashboard
            no_results = has_no_results(driver)
            session_checks += 1

            if not no_results:
                if not projects_alerted:
                    send_telegram(
                        f"🚨 Projects are available on CloudResearch!\n\n"
                        f"👉 {DASHBOARD_URL}"
                    )
                    projects_alerted = True
                else:
                    log.info("Projects still available (alert already sent).")
            else:
                if projects_alerted:
                    log.info("Back to no results, resetting alert flag.")
                    projects_alerted = False

        except WebDriverException as e:
            log.error(f"WebDriver error: {e}")
            logged_in = False

        except Exception as e:
            log.error(f"Unexpected error: {e}")

        wait_time = random.randint(CHECK_INTERVAL_MIN, CHECK_INTERVAL_MAX)
        log.info(f"Next check in {wait_time}s...")
        time.sleep(wait_time)


if __name__ == "__main__":
    monitor()