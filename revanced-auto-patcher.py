import argparse
import asyncio
import copy
import json
import logging
import os
import re
import subprocess
import sys
from typing import Optional, Dict, List

import playwright.async_api
import requests
from playwright.async_api import async_playwright
from requests import Session, Response

from __init__ import __version__

USER_AGENT: str = (r"Mozilla/5.0 (iPhone; CPU iPhone OS 14_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                   r"FxiOS/127.0 Mobile/15E148 Safari/605.1.15")
PLATFORM: str = sys.platform
MESSAGE: int = 100
VERSION_REGEX: str = r"^v\.?"


def main():
    parser = argparse.ArgumentParser(prog='revanced-auto-patcher',
                                     description='Automatically download and patch latest supported '
                                                 'YouTube Android app with ReVanced.')
    parser.add_argument("-v", "--version",
                        action="version",
                        version=f"%(prog)s v{__version__}")
    parser.add_argument("--init",
                        help="Prepare config file. This should be done only once, if you want to change any path do it "
                             "manually by editing the JSON file, \"init\" will reset everything in the config file.",
                        action="store_true")
    parser.add_argument("--conf",
                        help="Path to the configuration file. By default \"auto-patch.json\" in the current working "
                             "directory.",
                        default=[os.path.join(os.getcwd(), "auto-patch.json")],
                        nargs=1)
    parser.add_argument("--output",
                        help=f"Directory where the patched apk is stored. By default \"{os.path.sep}patched\""
                             f" in the current working directory. Only for use with \"--init\".",
                        nargs=1,
                        default=[os.path.join(os.getcwd(), "patched")])
    parser.add_argument("--store-path",
                        help=f"Where to store all the other files (ReVanced CLI, Patches, Original APK, etc). "
                             f"By default \"{os.path.sep}tmp\" in the current working directory. Only for use with "
                             f"\"--init\".",
                        default=[os.path.join(os.getcwd(), "tmp")],
                        nargs=1)
    parser.add_argument("--log-level",
                        help="How much stuff is logged. One of 'debug', 'info', 'warning', 'error'.",
                        default="warning",
                        choices=["debug", "info", "warning", "error"],
                        type=str.lower)

    args = parser.parse_args()

    init: bool = args.init
    conf: str = args.conf[0]
    output: str = args.output[0]
    store_path: str = args.store_path[0]

    log_level: str = logging.getLevelName(args.log_level.upper())
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s: %(message)s")
    logging.addLevelName(MESSAGE, "MESSAGE")

    if init:
        logging.info("Initializing configuration...")
        init_(conf, output, store_path)

    start_process(conf)


def init_(conf: str,
          output: str,
          store_path: str) -> None:
    config: Dict[str, str | Dict[str, str]] = {
        "Output": output,
        "Store_Path": store_path,
        "Versions": {
            "CLI": "",
            "Patches": "",
            "Patches_JSON": "",
            "Original_APK": "",
            "Integrations": ""
        },
        "Names": {
            "CLI": "",
            "Patches": "",
            "Patches_JSON": "",
            "Original_APK": "",
            "Integrations": ""
        }
    }

    with open(conf, "w", encoding="utf-8", errors="backslashreplace") as stream:
        json.dump(config, stream, indent=4)


def start_process(conf: str) -> None:
    if not os.path.exists(conf):
        logging.critical("Config file doesn't exist, you have to initialize.")
        sys.exit(1)
    elif not os.path.isfile(conf):
        logging.critical("Supplied config file path is not a file.")
        sys.exit(1)

    config: Dict[str, str | Dict[str, str]] = get_config(conf)
    latest_versions: Dict[str, Dict[str, Optional[str]]] = get_latest_versions()

    current_versions: Dict[str, str] = {
        "CLI": config["Versions"]["CLI"],
        "Patches": config["Versions"]["Patches"],
        "Patches_JSON": config["Versions"]["Patches_JSON"],
        "Integrations": config["Versions"]["Integrations"]
    }

    new_ver_available: bool = False
    tool: str
    for tool in current_versions.keys():
        if latest_versions[tool]["URL"] is None or latest_versions[tool]["Name"] is None:
            continue
        if latest_versions[tool]["Version"] != current_versions[tool]:
            logging.log(MESSAGE, f"New version found for {tool}: {latest_versions[tool]['Version']}.")
            old_version_path = os.path.join(config["Store_Path"], config["Names"][tool])
            new_ver_available = True
            download_latest_version(url=latest_versions[tool]["URL"],
                                    name=latest_versions[tool]["Name"],
                                    download_path=config["Store_Path"])
            if tool != "Patches_JSON" and config["Names"][tool] != "":
                try:
                    logging.info(f"Removing old version of {tool}...")
                    os.remove(old_version_path)
                except FileNotFoundError:
                    pass
        else:
            logging.log(MESSAGE, f"{tool} is already updated to the latest version.")

    if new_ver_available:
        write_new_versions_and_names(latest_versions, conf, config)

    current_yt_version: str = config["Versions"]["Original_APK"]
    latest_yt_version: str = get_latest_supported_yt_version(conf)

    if compare_versions(current_yt_version, latest_yt_version):
        logging.log(MESSAGE, "New YT version supported...")
        asyncio.run(download_latest_yt_apk(conf, latest_yt_version))
    else:
        logging.log(MESSAGE, "No new YT version is supported, latest version has been already patched.")
        sys.exit(0)

    yt_version: Dict[str, Dict[str, str]] = {
        "Original_APK": {
            "Name": f"com.google.android.youtube.{latest_yt_version}.apk",
            "Version": latest_yt_version
        }
    }

    write_new_versions_and_names(yt_version, conf, get_config(conf))
    patch_latest_yt_apk(conf)

    logging.log(MESSAGE, "All done.")


def get_config(conf: str) -> Dict[str, str | Dict[str, str]]:
    with open(conf, "r", encoding="utf-8", errors="backslashreplace") as stream:
        return copy.deepcopy(json.loads(stream.read()))


def get_latest_versions() -> Dict[str, Dict[str, Optional[str]]]:
    latest_cli: Dict[str, Optional[str]] = get_latest_cli()
    latest_patch_bundle: Dict[str, Optional[str]] = get_latest_patch_bundle()
    latest_patch_json: Dict[str, Optional[str]] = get_latest_patch_json()
    latest_integrations: Dict[str, Optional[str]] = get_latest_integrations_apk()

    return {
        "CLI": latest_cli,
        "Patches": latest_patch_bundle,
        "Patches_JSON": latest_patch_json,
        "Integrations": latest_integrations
    }


def get_latest_cli() -> Dict[str, Optional[str]]:
    url: str = "https://api.github.com/repos/revanced/revanced-cli/releases"

    newest_version: str
    newest_version_url: Optional[str]
    newest_version_name: Optional[str]
    newest_version, newest_version_url, newest_version_name = get_latest_version_name_and_url(url, "jar", "cli")

    return {
        "Name": newest_version_name,
        "Version": newest_version,
        "URL": newest_version_url
    }


def get_latest_patch_bundle() -> Dict[str, Optional[str]]:
    url: str = "https://api.github.com/repos/revanced/revanced-patches/releases"

    newest_version: str
    newest_version_url: Optional[str]
    newest_version_name: Optional[str]
    newest_version, newest_version_url, newest_version_name = get_latest_version_name_and_url(url,
                                                                                              "jar",
                                                                                              "patch bundle")

    return {
        "Name": newest_version_name,
        "Version": newest_version,
        "URL": newest_version_url
    }


def get_latest_patch_json() -> Dict[str, Optional[str]]:
    url: str = "https://api.github.com/repos/revanced/revanced-patches/releases"

    newest_version: str
    newest_version_url: Optional[str]
    newest_version_name: Optional[str]
    newest_version, newest_version_url, newest_version_name = get_latest_version_name_and_url(url, "json", "patch json")

    return {
        "Name": newest_version_name,
        "Version": newest_version,
        "URL": newest_version_url
    }


def get_latest_integrations_apk() -> Dict[str, Optional[str]]:
    url: str = "https://api.github.com/repos/revanced/revanced-integrations/releases"

    newest_version: str
    newest_version_url: Optional[str]
    newest_version_name: Optional[str]
    newest_version, newest_version_url, newest_version_name = get_latest_version_name_and_url(url,
                                                                                              "apk",
                                                                                              "integration apk")

    return {
        "Name": newest_version_name,
        "Version": newest_version,
        "URL": newest_version_url
    }


def get_latest_version_name_and_url(url: str,
                                    ext: str,
                                    tool_name: str) -> tuple[str, Optional[str], Optional[str]]:

    sess: Session = requests.session()
    sess.headers.update({
        "User-Agent": USER_AGENT
    })

    resp: Response = sess.get(url, allow_redirects=True)

    if not 199 < resp.status_code < 300:
        logging.critical(f"There was an error fetching the latest version for \"{tool_name}\": {resp.status_code}")
        sys.exit(1)

    json_data: dict = json.loads(resp.content)

    found_stable: bool = False
    i: int = 0
    i_max: int = len(json_data) - 1

    while not found_stable and i <= i_max:
        found_stable = not json_data[i]["prerelease"]
        i += 1

    if not found_stable:
        # No stable version found, using latest pre-release version
        newest_version = json_data[0]["tag_name"]
        newest_version_url, newest_version_name = get_download_url(json_data[0], ext, tool_name)
    else:
        newest_version = json_data[i - 1]["tag_name"]
        newest_version_url, newest_version_name = get_download_url(json_data[i - 1], ext, tool_name)

    return re.sub(VERSION_REGEX, "", newest_version), newest_version_url, newest_version_name,


def get_download_url(data: dict,
                     extension: str,
                     tool_name: str) -> tuple[Optional[str], Optional[str]]:
    item_number: int = len(data["assets"])

    if item_number == 0:
        logging.warning(f"No assets found for \"{tool_name}\"")
        return None, None,

    i: int
    for i in range(0, item_number):
        if data["assets"][i]["name"].lower().endswith(extension):
            return data["assets"][i]["browser_download_url"], data["assets"][i]["name"],

    logging.warning(f"No matching assets found for \"{tool_name}\" [expected extension: {extension}]")
    return None, None,


def download_latest_version(url: str,
                            name: str,
                            download_path: str) -> None:
    logging.log(MESSAGE, f"Downloading {name}...")

    name: str = sanitize_name(name)
    os.makedirs(download_path, exist_ok=True)

    session: Session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT
    })

    response: Response = session.get(url, stream=True)

    try:
        with open(os.path.join(download_path, name), "wb", buffering=0) as stream:
            chunk: bytes
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    stream.write(chunk)
    except OSError as e:
        logging.critical(f"There was an error downloading {name}: {e}", exc_info=True)
        sys.exit(1)

    logging.log(MESSAGE, "Finished downloading.")


def sanitize_name(name: str) -> str:
    illegal_characters: Dict[str, str] = {}

    if PLATFORM == "win32" or PLATFORM == "msys" or PLATFORM == "cygwin":
        illegal_characters = {
            "<": "\uFE64",
            ">": "\uFE65",
            ":": "\uFE55",
            "\"": "\uFF02",
            "/": "\uFF0F",
            "\\": "\uFF3C",
            "|": "\uFF5C",
            "?": "\uFF1F",
            "*": "\uFF0A"
        }

    if PLATFORM == "darwin" or PLATFORM == "linux":
        illegal_characters = {
            "/": "\uFF0F"
        }

    character: str
    for character in illegal_characters.keys():
        if character in name:
            name = name.replace(character, illegal_characters[character].encode("utf-8").decode("utf-8"))

    return name


def get_latest_supported_yt_version(conf: str) -> str:
    config: Dict[str, str | Dict[str, str]] = get_config(conf)

    json_path: str = os.path.join(config["Store_Path"], config["Names"]["Patches_JSON"])
    patches_json: List[dict] = json.load(open(json_path, "r", encoding="utf-8", errors="backslashreplace"))

    latest_versions: List[str] = []

    patch: dict
    for patch in patches_json:
        if patch.get("compatiblePackages") is not None:
            for package in patch["compatiblePackages"]:
                if package.get("name", "") == "com.google.android.youtube":
                    try:
                        last_index = len(package.get("versions", "")) - 1
                    except TypeError:
                        continue
                    version = package["versions"][last_index]
                    if version not in latest_versions:
                        latest_versions.append(version)
                    break

    if len(latest_versions) == 0:
        logging.critical("No supported version found for Youtube. "
                         "Unless something has changed since this program has been written, this shouldn't happen.")
        sys.exit(2)

    if len(latest_versions) == 1:
        return latest_versions[0]
    else:
        newest_found: str = ""
        for version_to_check in latest_versions:
            if compare_versions(version_to_check, newest_found):
                newest_found = version_to_check

        return newest_found


def compare_versions(current: str,
                     latest: str) -> bool:
    if current == "" or latest == "":
        return True

    current_version: str = re.sub(VERSION_REGEX, "", current)
    latest_version: str = re.sub(VERSION_REGEX, "", latest)

    current_version_split: List[str] = current_version.split(".")
    latest_version_list: List[str] = latest_version.split(".")

    if len(current_version_split) > len(latest_version_list):
        return True
    else:
        # if they have equal length, this will still return the correct number
        min_number: int = min(len(current_version_split), len(latest_version_list))

        return compare_version_numbers(current_version_split, latest_version_list, min_number)


def compare_version_numbers(current_version: list,
                            latest_version: list,
                            number: int) -> bool:
    """
    Should return True if "latest_version" is higher than "current_version".
    :param current_version: supposed old version
    :param latest_version: supposed new version
    :param number: number of items in the list that will be iterated
    :return: Whether the "latest_version" is higher than the "current_version"
    """
    for i in range(number):
        current_ver: str = current_version[i]
        latest_ver: str = latest_version[i]

        pad: int = max(len(current_ver), len(latest_ver)) - 1

        current_ver = end_fill(current_ver, pad)
        latest_ver = end_fill(latest_ver, pad)

        if int(current_ver) > int(latest_ver):
            return False
        elif int(current_ver) < int(latest_ver):
            return True
        else:
            continue

    return False


def write_new_versions_and_names(latest_versions: Dict[str, Dict[str, Optional[str]]],
                                 conf: str,
                                 config: Dict[str, str | Dict[str, str]]) -> None:
    new_config: Dict[str, str | Dict[str, str]] = copy.deepcopy(config)

    for tool in latest_versions.keys():
        new_config["Versions"][tool] = latest_versions[tool]["Version"]
        new_config["Names"][tool] = sanitize_name(latest_versions[tool]["Name"])

    with open(conf, "w", encoding="utf-8") as stream:
        json.dump(new_config, stream, indent=4)


def end_fill(string: str,
             amount: int) -> str:
    if len(string) >= amount:
        return string
    else:
        return string + "0" * (amount - len(string))


async def download_latest_yt_apk(conf: str,
                                 version: str) -> None:
    url: str = f"https://apkpure.com/youtube/com.google.android.youtube/download/{version}"
    config: Dict[str, str | Dict[str, str]] = get_config(conf)
    error_occurred: bool = False

    logging.log(MESSAGE, "Downloading latest YouTube APK...")

    install_playwright()

    async with async_playwright() as p:
        browser = await p.firefox.launch()
        page = await browser.new_page()
        try:
            resp = await page.goto(url)

            if resp.status != 200:
                logging.error(f"There was an error retrieving the app page. HTTP error: {resp.status}")
                sys.exit(1)

            async with page.expect_download() as downloader:
                await page.click("a.download-start-btn", button="left", timeout=360000)
            download = await downloader.value
            await download.save_as(os.path.join(config["Store_Path"], f"com.google.android.youtube.{version}.apk"))
        except playwright.async_api.TimeoutError as e:
            logging.critical(f"Timeout error: {e}", exc_info=True)
            error_occurred = True
        except playwright.async_api.Error as e:
            logging.critical(f"Playwright error: {e}", exc_info=True)
            error_occurred = True
        except OSError as e:
            logging.error(f"There was an error saving the APK file: {e}", exc_info=True)
            error_occurred = True
        finally:
            await browser.close()

            if error_occurred:
                sys.exit(1)

        logging.log(MESSAGE, "Finished downloading.")


def patch_latest_yt_apk(conf: str) -> None:
    logging.log(MESSAGE, "Patching latest YouTube APK...")
    config: Dict[str, str | Dict[str, str]] = get_config(conf)

    cli_path: str = os.path.join(config["Store_Path"], config["Names"]["CLI"])
    patches_path: str = os.path.join(config["Store_Path"], config["Names"]["Patches"])
    integrations_path: str = os.path.join(config["Store_Path"], config["Names"]["Integrations"])
    output_file: str = os.path.join(config["Output"],
                                    f"app.revanced.android.youtube.{config['Versions']['Original_APK']}.apk")
    input_file: str = os.path.join(config["Store_Path"], config["Names"]["Original_APK"])

    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    command = ["java",
               "-jar",
               cli_path,
               "patch",
               "--purge",
               "--patch-bundle",
               patches_path,
               "--merge",
               integrations_path,
               "--out",
               output_file,
               input_file
               ]

    try:
        subprocess.run(command, encoding="utf-8", check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        logging.critical("Error patching APK:")
        logging.critical(e, exc_info=True)
        sys.exit(1)

    logging.log(MESSAGE, "Patching finished.")


def install_playwright() -> None:
    cmd: List[str] = [
        "playwright",
        "install",
        "firefox"
    ]

    try:
        subprocess.run(cmd, encoding="utf-8", check=True)
    except subprocess.CalledProcessError as e:
        logging.critical("There was an error installing playwright:")
        logging.critical(e, exc_info=True)
        sys.exit(3)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Cancel requested, exiting...")
        sys.exit(5)
