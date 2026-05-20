import os
import time
import logging
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

# ─────────────────────────────────────────────
# CONFIGURATION — fill these in
# ─────────────────────────────────────────────
EMAIL = os.environ.get("CR_EMAIL", "your_email@example.com")
PASSWORD = os.environ.get("CR_PASSWORD", "your_password")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "your_bot_token")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8552808380")

CHECK_INTERVAL = 120  # seconds between checks (2 minutes)
DASHBOARD_URL = "https://connect.cloudresearch.com/participant/dashboard"
LOGIN_URL = "https://connect.cloudresearch.com/participant/login"
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def send_telegram(message: str):
    """Send a Telegram message to your phone."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
        resp.raise_for_status()
        log.info("Telegram notification sent.")
    except Exception as e:
        log.error(f"Failed to send Telegram message: {e}")


def make_driver() -> webdriver.Chrome:
    """Create a headless Chrome driver."""
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    return webdriver.Chrome(options=opts)


def login(driver: webdriver.Chrome) -> bool:
    """Log in to CloudResearch. Returns True on success."""
    try:
        log.info("Navigating to login page...")
        driver.get(LOGIN_URL)
        wait = WebDriverWait(driver, 20)

        # Fill email
        email_field = wait.until(EC.presence_of_element_located((By.ID, "Email")))
        email_field.clear()
        email_field.send_keys(EMAIL)

        # Fill password
        password_field = driver.find_element(By.ID, "Password")
        password_field.clear()
        password_field.send_keys(PASSWORD)

        # Submit
        submit = driver.find_element(By.ID, "log-in-btn")
        submit.click()

        # Wait for dashboard to load
        wait.until(EC.url_contains("/dashboard"))
        log.info("Login successful.")
        return True

    except TimeoutException:
        log.error("Login timed out — check credentials or page structure changed.")
        return False
    except Exception as e:
        log.error(f"Login error: {e}")
        return False


def get_projects(driver: webdriver.Chrome) -> list[str]:
    """
    Scrape the dashboard for available projects.
    Returns a list of project identifiers (titles or IDs).
    Adjust the CSS selectors below if the page structure differs.
    """
    try:
        driver.get(DASHBOARD_URL)
        wait = WebDriverWait(driver, 20)

        # Wait for project content to load
        # These selectors are best guesses — may need adjusting after inspecting the page
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "body")))
        time.sleep(3)  # let JS render

        # Try common patterns for project listings
        project_elements = driver.find_elements(By.CSS_SELECTOR,
            ".study-card, .project-card, .task-card, "
            "[class*='study'], [class*='project'], [class*='task'], "
            "table tbody tr, .hit-card"
        )

        projects = []
        for el in project_elements:
            text = el.text.strip()
            if text and len(text) > 5:
                # Use first 80 chars as identifier
                projects.append(text[:80])

        log.info(f"Found {len(projects)} project element(s) on dashboard.")
        return projects

    except Exception as e:
        log.error(f"Error scraping dashboard: {e}")
        return []


def monitor():
    """Main monitoring loop."""
    log.info("Starting CloudResearch monitor...")
    send_telegram("🟢 CloudResearch monitor started! I'll notify you when new projects appear.")

    driver = None
    known_projects: set[str] = set()
    logged_in = False
    session_checks = 0
    MAX_CHECKS_BEFORE_RELOGIN = 30  # re-login every ~1 hour

    while True:
        try:
            # Create driver or re-login periodically
            if driver is None or not logged_in or session_checks >= MAX_CHECKS_BEFORE_RELOGIN:
                if driver:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                driver = make_driver()
                logged_in = login(driver)
                session_checks = 0

                if not logged_in:
                    send_telegram("⚠️ CloudResearch login failed. Will retry in 5 minutes.")
                    driver.quit()
                    driver = None
                    time.sleep(300)
                    continue

            # Scrape projects
            current_projects = get_projects(driver)
            session_checks += 1

            if not current_projects and session_checks == 1:
                # First run, selectors might need tuning
                log.warning("No projects found on first check — selectors may need adjusting.")
                send_telegram(
                    "⚠️ Monitor connected but found 0 projects on first check.\n"
                    "The page may need selector tuning. Check logs."
                )

            current_set = set(current_projects)

            if known_projects:  # skip diff on very first run
                new_projects = current_set - known_projects
                if new_projects:
                    msg = f"🚨 New project(s) on CloudResearch!\n\n"
                    for p in new_projects:
                        msg += f"• {p}\n"
                    msg += f"\n👉 {DASHBOARD_URL}"
                    log.info(f"NEW PROJECTS DETECTED: {new_projects}")
                    send_telegram(msg)
                else:
                    log.info("No new projects.")
            else:
                log.info(f"Baseline set: {len(current_set)} project(s) recorded.")

            known_projects = current_set

        except WebDriverException as e:
            log.error(f"WebDriver error: {e}")
            logged_in = False  # force re-login next iteration

        except Exception as e:
            log.error(f"Unexpected error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    monitor()
