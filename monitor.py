import os
import time
import random
import logging
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

# CONFIGURATION
EMAIL          = os.environ.get("CR_EMAIL", "your_email@example.com")
PASSWORD       = os.environ.get("CR_PASSWORD", "your_password")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "your_bot_token")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8552808380")

DASHBOARD_URL  = "https://connect.cloudresearch.com/participant/dashboard"
LOGIN_URL      = "https://account.cloudresearch.com/Account/Login?AppDestination=Connect"

# Check every 90–150 seconds (randomised)
CHECK_INTERVAL_MIN = 90
CHECK_INTERVAL_MAX = 150

# Re-login every 2–4 hours (randomised), in number of checks
RELOGIN_AFTER_MIN = int((2 * 3600) / CHECK_INTERVAL_MAX)
RELOGIN_AFTER_MAX = int((4 * 3600) / CHECK_INTERVAL_MIN)

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


def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
        resp.raise_for_status()
        log.info("Telegram notification sent.")
    except Exception as e:
        log.error(f"Failed to send Telegram message: {e}")


def human_delay(min_s=0.5, max_s=1.5):
    """Random short delay to mimic human interaction."""
    time.sleep(random.uniform(min_s, max_s))


def make_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(f"user-agent={random.choice(USER_AGENTS)}")
    # Stealth flags to hide headless detection
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=opts)
    # Patch navigator.webdriver to undefined
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

        # Step 1: Dismiss cookie banner if present
        try:
            cookie_btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(text(), 'Accept')]")
            ))
            cookie_btn.click()
            log.info("Cookie banner dismissed.")
            human_delay(0.5, 1.5)
        except Exception:
            log.info("No cookie banner, continuing.")

        # Step 2: Click Connect for Participants card
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

        # Step 3: Fill email (type like a human)
        email_field = wait.until(EC.element_to_be_clickable((By.ID, "Email")))
        driver.execute_script("arguments[0].scrollIntoView(true);", email_field)
        human_delay(0.3, 0.8)
        email_field.clear()
        for char in EMAIL:
            email_field.send_keys(char)
            time.sleep(random.uniform(0.03, 0.12))
        log.info("Email entered.")
        human_delay(0.3, 0.8)

        # Step 4: Fill password
        password_field = wait.until(EC.element_to_be_clickable((By.ID, "Password")))
        password_field.clear()
        for char in PASSWORD:
            password_field.send_keys(char)
            time.sleep(random.uniform(0.03, 0.12))
        log.info("Password entered.")
        human_delay(0.5, 1.2)

        # Step 5: Submit
        submit = wait.until(EC.element_to_be_clickable((By.ID, "log-in-btn")))
        driver.execute_script("arguments[0].click();", submit)
        log.info("Submit clicked, waiting for redirect...")

        # Step 6: Wait for redirect
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
    """
    Returns True if the 'No results' message is visible on the dashboard.
    Returns False if it's gone (meaning projects are available!).
    """
    try:
        driver.get(DASHBOARD_URL)
        wait = WebDriverWait(driver, 20)
        # Wait for page body to load
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        human_delay(3, 5)  # let JS render fully

        log.info(f"Dashboard loaded: {driver.current_url}")

        # Look for the "No results" paragraph
        no_results_elements = driver.find_elements(
            By.XPATH, "//*[normalize-space(text())='No results']"
        )

        if no_results_elements:
            log.info("'No results' found — no new projects yet.")
            return True
        else:
            log.info("'No results' NOT found — projects may be available!")
            return False

    except Exception as e:
        log.error(f"Error checking dashboard: {e}")
        driver.save_screenshot("dashboard_error.png")
        return True  # assume no results on error to avoid false alerts


def monitor():
    log.info("Starting CloudResearch monitor...")
    send_telegram("Hi! I'll ping you when the secret that we discussed happens ;)")

    driver = None
    logged_in = False
    session_checks = 0
    relogin_after = random.randint(RELOGIN_AFTER_MIN, RELOGIN_AFTER_MAX)
    projects_alerted = False  # avoid spamming if projects stay visible

    while True:
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
                    send_telegram("CloudResearch login failed. Retrying in 5 minutes.")
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
                        f"Projects are available on CloudResearch!\n\n"
                        f"{DASHBOARD_URL}"
                    )
                    projects_alerted = True
                else:
                    log.info("Projects still available (alert already sent).")
            else:
                # Reset alert flag once "No results" comes back
                if projects_alerted:
                    log.info("Projects gone again, resetting alert flag.")
                    projects_alerted = False

        except WebDriverException as e:
            log.error(f"WebDriver error: {e}")
            logged_in = False

        except Exception as e:
            log.error(f"Unexpected error: {e}")

        # Random wait between checks
        wait_time = random.randint(CHECK_INTERVAL_MIN, CHECK_INTERVAL_MAX)
        log.info(f"Next check in {wait_time}s...")
        time.sleep(wait_time)


if __name__ == "__main__":
    monitor()