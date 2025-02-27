import logging
import platform
import sys
import time
from datetime import timedelta
from urllib.parse import unquote

from selenium_fetch import fetch, Options, get_browser_user_agent

from func_timeout import FunctionTimedOut, func_timeout
from selenium.common import TimeoutException
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.expected_conditions import (
    presence_of_element_located, staleness_of, title_is)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.wait import WebDriverWait

import utils
from dtos import (STATUS_ERROR, STATUS_OK, ChallengeResolutionResultT,
                  ChallengeResolutionT, HealthResponse, IndexResponse,
                  V1RequestBase, V1ResponseBase)
from sessions import SessionsStorage

ACCESS_DENIED_TITLES = [
    # Cloudflare
    'Access denied',
    # Cloudflare http://bitturk.net/ Firefox
    'Attention Required! | Cloudflare'
]
ACCESS_DENIED_SELECTORS = [
    # Bing
    'input[id^=cf-chl-widget]:not([value])',
    # Cloudflare
    'div.cf-error-title span.cf-code-label span',
    # Cloudflare http://bitturk.net/ Firefox
    '#cf-error-details div.cf-error-overview h1'
]
CHALLENGE_TITLES = [
    # Cloudflare
    'Just a moment...',
    # DDoS-GUARD
    'DDoS-Guard'
]
CHALLENGE_SELECTORS = [
    # Cloudflare
    '#cf-challenge-running', '.ray_id', '.attack-box', '#cf-please-wait', '#challenge-spinner', '#trk_jschal_js',
    # Custom CloudFlare for EbookParadijs, Film-Paleis, MuziekFabriek and Puur-Hollands
    'td.info #js_info',
    # Fairlane / pararius.com
    'div.vc div.text-box h2'
]
SHORT_TIMEOUT = 1
SESSIONS_STORAGE = SessionsStorage()


def test_browser_installation():
    logging.info("Testing web browser installation...")
    logging.info("Platform: " + platform.platform())

    chrome_exe_path = utils.get_chrome_exe_path()
    if chrome_exe_path is None:
        logging.error("Chrome / Chromium web browser not installed!")
        sys.exit(1)
    else:
        logging.info("Chrome / Chromium path: " + chrome_exe_path)

    chrome_major_version = utils.get_chrome_major_version()
    if chrome_major_version == None:
        logging.error("Chrome / Chromium version not detected!")
        sys.exit(1)
    else:
        logging.info("Chrome / Chromium major version: " + str(chrome_major_version))

    logging.info("Launching web browser...")
    user_agent = utils.get_user_agent()
    logging.info("FlareSolverr User-Agent: " + user_agent)
    logging.info("Test successful!")


def index_endpoint() -> IndexResponse:
    res = IndexResponse({})
    res.msg = "FlareSolverr is ready!"
    res.version = utils.get_flaresolverr_version()
    res.userAgent = utils.get_user_agent()
    return res


def health_endpoint() -> HealthResponse:
    res = HealthResponse({})
    res.status = STATUS_OK
    return res


def controller_v1_endpoint(req: V1RequestBase) -> V1ResponseBase:
    start_ts = int(time.time() * 1000)
    logging.info(f"Incoming request => POST /v1 body: {utils.object_to_dict(req)}")
    res: V1ResponseBase
    try:
        res = _controller_v1_handler(req)
    except Exception as e:
        res = V1ResponseBase({})
        res.__error_500__ = True
        res.status = STATUS_ERROR
        res.message = "Error: " + str(e)
        logging.error(res.message)

    res.startTimestamp = start_ts
    res.endTimestamp = int(time.time() * 1000)
    res.version = utils.get_flaresolverr_version()
    logging.debug(f"Response => POST /v1 body: {utils.object_to_dict(res)}")
    logging.info(f"Response in {(res.endTimestamp - res.startTimestamp) / 1000} s")
    return res


def _controller_v1_handler(req: V1RequestBase) -> V1ResponseBase:
    # do some validations
    if req.cmd is None:
        raise Exception("Request parameter 'cmd' is mandatory.")
    if req.headers is not None:
        logging.warning("Request parameter 'headers' was removed in FlareSolverr v2.")
    if req.userAgent is not None:
        logging.warning("Request parameter 'userAgent' was removed in FlareSolverr v2.")

    # set default values
    if req.maxTimeout is None or req.maxTimeout < 1:
        req.maxTimeout = 600000 #10 mins

    # execute the command
    res: V1ResponseBase
    if req.cmd == 'sessions.create':
        res = _cmd_sessions_create(req)
    elif req.cmd == 'sessions.list':
        res = _cmd_sessions_list(req)
    elif req.cmd == 'sessions.destroy':
        res = _cmd_sessions_destroy(req)
    elif req.cmd == 'request.get':
        res = _cmd_request_get(req)
    elif req.cmd == 'request.post':
        res = _cmd_request_post(req)
    elif req.cmd == 'request.put':
        res = _cmd_request_put(req)
    elif req.cmd == 'request.patch':
        res = _cmd_request_patch(req)
    elif req.cmd == 'request.delete':
        res = _cmd_request_delete(req)
    else:
        raise Exception(f"Request parameter 'cmd' = '{req.cmd}' is invalid.")

    return res


def _cmd_request_get(req: V1RequestBase) -> V1ResponseBase:
    # do some validations
    if req.url is None:
        raise Exception("Request parameter 'url' is mandatory in 'request.get' command.")
    if req.postData is not None:
        raise Exception("Cannot use 'postBody' when sending a GET request.")
    if req.returnRawHtml is not None:
        logging.warning("Request parameter 'returnRawHtml' was removed in FlareSolverr v2.")
    if req.download is not None:
        logging.warning("Request parameter 'download' was removed in FlareSolverr v2.")

    challenge_res = _resolve_challenge(req, 'GET')
    res = V1ResponseBase({})
    res.status = challenge_res.status
    res.message = challenge_res.message
    res.solution = challenge_res.result
    return res


def _cmd_request_post(req: V1RequestBase) -> V1ResponseBase:
    # do some validations
    if req.postData is None:
        raise Exception("Request parameter 'postData' is mandatory in 'request.post' command.")
    if req.returnRawHtml is not None:
        logging.warning("Request parameter 'returnRawHtml' was removed in FlareSolverr v2.")
    if req.download is not None:
        logging.warning("Request parameter 'download' was removed in FlareSolverr v2.")

    challenge_res = _resolve_challenge(req, 'POST')
    res = V1ResponseBase({})
    res.status = challenge_res.status
    res.message = challenge_res.message
    res.solution = challenge_res.result
    return res


def _cmd_request_put(req: V1RequestBase) -> V1ResponseBase:
    # do some validations
    if req.postData is None:
        raise Exception("Request parameter 'postData' is mandatory in 'request.put' command.")
    if req.returnRawHtml is not None:
        logging.warning("Request parameter 'returnRawHtml' was removed in FlareSolverr v2.")
    if req.download is not None:
        logging.warning("Request parameter 'download' was removed in FlareSolverr v2.")

    challenge_res = _resolve_challenge(req, 'PUT')
    res = V1ResponseBase({})
    res.status = challenge_res.status
    res.message = challenge_res.message
    res.solution = challenge_res.result
    return res


def _cmd_request_patch(req: V1RequestBase) -> V1ResponseBase:
    # do some validations
    if req.postData is None:
        raise Exception("Request parameter 'postData' is mandatory in 'request.put' command.")
    if req.returnRawHtml is not None:
        logging.warning("Request parameter 'returnRawHtml' was removed in FlareSolverr v2.")
    if req.download is not None:
        logging.warning("Request parameter 'download' was removed in FlareSolverr v2.")

    challenge_res = _resolve_challenge(req, 'PATCH')
    res = V1ResponseBase({})
    res.status = challenge_res.status
    res.message = challenge_res.message
    res.solution = challenge_res.result
    return res


def _cmd_request_delete(req: V1RequestBase) -> V1ResponseBase:
    # do some validations
    if req.postData is None:
        raise Exception("Request parameter 'postData' is mandatory in 'request.put' command.")
    if req.returnRawHtml is not None:
        logging.warning("Request parameter 'returnRawHtml' was removed in FlareSolverr v2.")
    if req.download is not None:
        logging.warning("Request parameter 'download' was removed in FlareSolverr v2.")

    challenge_res = _resolve_challenge(req, 'DELETE')
    res = V1ResponseBase({})
    res.status = challenge_res.status
    res.message = challenge_res.message
    res.solution = challenge_res.result
    return res


def _cmd_sessions_create(req: V1RequestBase) -> V1ResponseBase:
    logging.debug("Creating new session...")

    session, fresh = SESSIONS_STORAGE.create(session_id=req.session, proxy=req.proxy)
    session_id = session.session_id

    if not fresh:
        return V1ResponseBase({
            "status": STATUS_OK,
            "message": "Session already exists.",
            "session": session_id
        })

    return V1ResponseBase({
        "status": STATUS_OK,
        "message": "Session created successfully.",
        "session": session_id
    })


def _cmd_sessions_list(req: V1RequestBase) -> V1ResponseBase:
    session_ids = SESSIONS_STORAGE.session_ids()

    return V1ResponseBase({
        "status": STATUS_OK,
        "message": "",
        "sessions": session_ids
    })


def _cmd_sessions_destroy(req: V1RequestBase) -> V1ResponseBase:
    session_id = req.session
    existed = SESSIONS_STORAGE.destroy(session_id)

    if not existed:
        raise Exception("The session doesn't exist.")

    return V1ResponseBase({
        "status": STATUS_OK,
        "message": "The session has been removed."
    })


def _resolve_challenge(req: V1RequestBase, method: str) -> ChallengeResolutionT:
    timeout = req.maxTimeout / 1000
    driver = None
    try:
        if req.session:
            session_id = req.session
            ttl = timedelta(minutes=req.session_ttl_minutes) if req.session_ttl_minutes else None
            session, fresh = SESSIONS_STORAGE.get(session_id, ttl)

            if fresh:
                logging.info(f"new session created to perform the request (session_id={session_id})")
            else:
                logging.info(f"existing session is used to perform the request (session_id={session_id}, "
                              f"lifetime={str(session.lifetime())}, ttl={str(ttl)})")
            driver = session.driver

        if driver is None:
            logging.info('> New instance of webdriver has been created to perform the request')
            driver = utils.get_webdriver(req.proxy)
        
        if driver.session_id == None:
            logging.info('> Driver session was None. Start new one.')
            # driver.start_session()
        return func_timeout(timeout, _evil_logic, (req, driver, method))
    except FunctionTimedOut:
        raise Exception(f'Error solving the challenge. Timeout after {timeout} seconds.')
    except Exception as e:
        raise Exception('Error solving the challenge. ' + str(e).replace('\n', '\\n'))
    finally:
        if not req.session and driver is not None:
            driver.quit()
            logging.info('A used instance of webdriver has been destroyed')


def click_verify(driver: WebDriver):
    try:
        logging.debug("Try to find the Cloudflare verify checkbox...")
        iframe = driver.find_element(By.XPATH, "//iframe[starts-with(@id, 'cf-chl-widget-')]")
        driver.switch_to.frame(iframe)
        checkbox = driver.find_element(
            by=By.XPATH,
            value='//*[@id="challenge-stage"]/div/label/input',
        )
        if checkbox:
            actions = ActionChains(driver)
            actions.move_to_element_with_offset(checkbox, 5, 7)
            actions.click(checkbox)
            actions.perform()
            logging.debug("Cloudflare verify checkbox found and clicked!")
    except Exception:
        logging.debug("Cloudflare verify checkbox not found on the page.")
    finally:
        driver.switch_to.default_content()

    try:
        logging.debug("Try to find the Cloudflare 'Verify you are human' button...")
        button = driver.find_element(
            by=By.XPATH,
            value="//input[@type='button' and @value='Verify you are human']",
        )
        if button:
            actions = ActionChains(driver)
            actions.move_to_element_with_offset(button, 5, 7)
            actions.click(button)
            actions.perform()
            logging.debug("The Cloudflare 'Verify you are human' button found and clicked!")
    except Exception:
        logging.debug("The Cloudflare 'Verify you are human' button not found on the page.")

    time.sleep(2)


def _evil_logic(req: V1RequestBase, driver: WebDriver, method: str) -> ChallengeResolutionT:
    res = ChallengeResolutionT({})
    res.status = STATUS_OK
    res.message = ""

    # driver.start_session(driver.capabilities)

    fetchResponse = None
    # navigate to the page
    logging.debug(f'Navigating to... {req.url}')
    #driver.get(req.url)
    #driver.start_session()

    # set cookies if required
    if req.cookies is not None and len(req.cookies) > 0:
        # setting correct context for cookies
        driver.get(req.apiUrl)
        driver.start_session()
        logging.debug(f'Setting cookies...')
        for cookie in req.cookies:
            driver.delete_cookie(cookie['name'])
            driver.add_cookie(cookie)
        # load page with given cookies
        if method == 'GET':
            driver.get(req.url)
            driver.start_session()  # required to bypass Cloudflare
    elif method == 'GET':
        driver.get(req.url)
        driver.start_session()  # required to bypass Cloudflare
    
    # execute fetch call
    if method == 'POST':
        fetchResponse = _post_request(req, driver)
    elif method == 'PUT':
        fetchResponse = _put_request(req, driver)
    elif method == 'PATCH':
        fetchResponse = _patch_request(req, driver)
    elif method == 'DELETE':
        fetchResponse = _delete_request(req, driver)

    # wait for the page
    if utils.get_config_log_html():
        logging.debug(f"Response HTML:\n{driver.page_source}")
    html_element = driver.find_element(By.TAG_NAME, "html")
    page_title = driver.title

    # find access denied titles
    for title in ACCESS_DENIED_TITLES:
        if title == page_title:
            raise Exception('Cloudflare has blocked this request. '
                            'Probably your IP is banned for this site, check in your web browser.')
    # find access denied selectors
    for selector in ACCESS_DENIED_SELECTORS:
        found_elements = driver.find_elements(By.CSS_SELECTOR, selector)
        if len(found_elements) > 0:
            raise Exception('Cloudflare has blocked this request. '
                            'Probably your IP is banned for this site, check in your web browser.')

    # find challenge by title
    challenge_found = False
    for title in CHALLENGE_TITLES:
        if title.lower() == page_title.lower():
            challenge_found = True
            logging.info("Challenge detected. Title found: " + page_title)
            break
    if not challenge_found:
        # find challenge by selectors
        for selector in CHALLENGE_SELECTORS:
            found_elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if len(found_elements) > 0:
                challenge_found = True
                logging.info("Challenge detected. Selector found: " + selector)
                break

    attempt = 0
    if challenge_found:
        while True:
            try:
                attempt = attempt + 1
                # wait until the title changes
                for title in CHALLENGE_TITLES:
                    logging.debug("Waiting for title (attempt " + str(attempt) + "): " + title)
                    WebDriverWait(driver, SHORT_TIMEOUT).until_not(title_is(title))

                # then wait until all the selectors disappear
                for selector in CHALLENGE_SELECTORS:
                    logging.debug("Waiting for selector (attempt " + str(attempt) + "): " + selector)
                    WebDriverWait(driver, SHORT_TIMEOUT).until_not(presence_of_element_located((By.CSS_SELECTOR, selector)))

                # all elements not found
                break

            except TimeoutException:
                logging.debug("Timeout waiting for selector")

                click_verify(driver)

                # update the html (cloudflare reloads the page every 5 s)
                html_element = driver.find_element(By.TAG_NAME, "html")

        # waits until cloudflare redirection ends
        logging.debug("Waiting for redirect")
        # noinspection PyBroadException
        try:
            WebDriverWait(driver, SHORT_TIMEOUT).until(staleness_of(html_element))
        except Exception:
            logging.debug("Timeout waiting for redirect")

        logging.info("Challenge solved!")
        res.message = "Challenge solved!"
    else:
        logging.info("Challenge not detected!")
        res.message = "Challenge not detected!"

    challenge_res = ChallengeResolutionResultT({})
    challenge_res.url = driver.current_url
    challenge_res.status = 200  # todo: fix, selenium not provides this info
    challenge_res.cookies = driver.get_cookies()
    challenge_res.userAgent = utils.get_user_agent(driver)

    if not req.returnOnlyCookies:
        challenge_res.headers = {}  # todo: fix, selenium not provides this info
        challenge_res.response = driver.page_source

    if fetchResponse != None and fetchResponse.status:
        status = {}
        statusTxts = str(fetchResponse.status).split(" ")
        for statusTxt in statusTxts:
            keyValue = statusTxt.split('=')
            if len(keyValue[1]) == "''":
                keyValue[1] = ""
            status[keyValue[0]] = keyValue[1]
        challenge_res.status = int(status["code"])
        if "test" in status and len(status["text"]) > 0:
            challenge_res.response = status["text"]
        else:
            challenge_res.response = fetchResponse.text
    
    # logging.info("response.status: " + str(challenge_res.status))
    # logging.info("response: " + str(challenge_res.response))

    res.result = challenge_res
    return res


def _post_request_old(req: V1RequestBase, driver: WebDriver):
    post_form = f'<form id="hackForm" action="{req.url}" method="POST">'
    query_string = req.postData if req.postData[0] != '?' else req.postData[1:]
    pairs = query_string.split('&')
    for pair in pairs:
        parts = pair.split('=')
        # noinspection PyBroadException
        try:
            name = unquote(parts[0])
        except Exception:
            name = parts[0]
        if name == 'submit':
            continue
        # noinspection PyBroadException
        try:
            value = unquote(parts[1])
        except Exception:
            value = parts[1]
        post_form += f'<input type="text" name="{name}" value="{value}"><br>'
    post_form += '</form>'
    html_content = f"""
        <!DOCTYPE html>
        <html>
        <body>
            {post_form}
            <script>document.getElementById('hackForm').submit();</script>
        </body>
        </html>"""
    driver.get("data:text/html;charset=utf-8," + html_content)
    # driver.start_session()  # required to bypass Cloudflare


def _fetch_request(method: str, req: V1RequestBase, driver: WebDriver):
    driver.get(req.url)
    # driver.start_session()

    headers = {
        "user-agent": get_browser_user_agent(driver),
        'origin': req.url,
        'referer': req.url,
        'Content-Length': len(req.postData),
        'Content-Type': 'application/json'
    }

    if req.headers != None:
        for header in req.headers:
            print("+ " + header)
            headers.update(header)

    cookieStr = ""
    for cookie in driver.get_cookies():
        cookieStr += (cookie["name"] + "=" + str(cookie["value"]) + "; ")

    # for cookie in driver.get_cookies():
    #     cookie.
    if cookieStr != "":
        cookieStr = cookieStr[:-2]
        headers.update({'Cookie': cookieStr})

    options = Options(method=method, headers=headers, body=req.postData)
    response = fetch(driver, req.apiUrl, options)
    logging.info("fetchResponse: " + str(response))
    logging.info("fetchResponse.status: " + str(response.status))
    logging.info("fetchResponse.text: " + str(response.text))

    driver.start_session()  # required to bypass Cloudflare
    return response


def _post_request(req: V1RequestBase, driver: WebDriver):
    return _fetch_request("POST", req, driver)


def _put_request(req: V1RequestBase, driver: WebDriver):
    return _fetch_request("PUT", req, driver)


def _patch_request(req: V1RequestBase, driver: WebDriver):
    return _fetch_request("PATCH", req, driver)


def _delete_request(req: V1RequestBase, driver: WebDriver):
    return _fetch_request("DELETE", req, driver)
