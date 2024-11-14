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
    parser.add_argument("--include-beta",
                        help="Include beta (preview) versions when searching for updates.",
                        action="store_true")
    parser.add_argument("--force-patch",
                        help="Force patch YouTube apk file even if no new supported YT version is found.",
                        action="store_true")
    parser.add_argument("--log-level",
                        help="How much stuff is logged. One of 'debug', 'info', 'warning', 'error'.",
                        default="warning",
                        choices=["debug", "info", "warning", "error"],
                        type=str.lower)

    args = parser.parse_args()

    init: bool = args.init
    config_path: str = args.conf[0]
    output: str = args.output[0]
    store_path: str = args.store_path[0]
    include_beta: bool = args.include_beta
    force_patch: bool = args.force_patch

    log_level: str = logging.getLevelName(args.log_level.upper())
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s: %(message)s")
    logging.addLevelName(MESSAGE, "MESSAGE")

    if init:
        logging.info("Initializing configuration...")
        init_(config_path, output, store_path)

    start_process(config_path=config_path,
                  include_beta=include_beta,
                  force_patch=force_patch)


def init_(conf: str,
          output: str,
          store_path: str) -> None:
    config: Dict[str, str | Dict[str, str]] = {
        "Output": output,
        "Store_Path": store_path,
        "Versions": {
            "CLI": "",
            "Patches": "",
            "Original_APK": "",
        },
        "Names": {
            "CLI": "",
            "Patches": "",
            "Original_APK": "",
        }
    }

    with open(conf, "w", encoding="utf-8", errors="backslashreplace") as stream:
        json.dump(config, stream, indent=4)


def start_process(config_path: str,
                  include_beta: bool,
                  force_patch : bool) -> None:
    if not os.path.exists(config_path):
        logging.critical("Config file doesn't exist, you have to initialize.")
        sys.exit(1)
    elif not os.path.isfile(config_path):
        logging.critical("Supplied config file path is not a file.")
        sys.exit(1)

    config_data: Dict[str, str | Dict[str, str | Dict[str, str | bool]]] = get_config(config_path=config_path)
    latest_versions: Dict[str, Dict[str, Optional[str]]] = get_latest_versions(include_beta=include_beta)

    current_versions: Dict[str, str] = {
        "CLI": config_data["Versions"]["CLI"],
        "Patches": config_data["Versions"]["Patches"],
    }

    new_ver_available: bool = False
    tool: str
    for tool in current_versions.keys():
        if latest_versions[tool]["URL"] is None or latest_versions[tool]["Name"] is None:
            continue
        if latest_versions[tool]["Version"] != current_versions[tool]:
            logging.log(MESSAGE, f"New version found for {tool}: {latest_versions[tool]['Version']}.")
            old_version_path = os.path.join(config_data["Store_Path"], config_data["Names"][tool])
            new_ver_available = True
            download_latest_version(url=latest_versions[tool]["URL"],
                                    name=latest_versions[tool]["Name"],
                                    download_path=config_data["Store_Path"])
            if config_data["Names"][tool] != "":
                try:
                    logging.info(f"Removing old version of {tool}...")
                    os.remove(old_version_path)
                except FileNotFoundError:
                    pass
                except PermissionError:
                    logging.warning(f"There was an error removing the file: {old_version_path}")
        else:
            logging.log(MESSAGE, f"{tool} is already updated to the latest version.")

    if new_ver_available:
        write_new_versions_and_names(latest_versions=latest_versions,
                                     config_path=config_path,
                                     config_data=config_data)

    current_yt_version: str = config_data["Versions"]["Original_APK"]
    latest_yt_version: str = get_latest_supported_yt_version(config_path)

    if compare_versions(version_to_check=latest_yt_version, latest_version_found=current_yt_version):
        logging.log(MESSAGE, "New YT version supported...")
        asyncio.run(download_latest_yt_apk(config_path, latest_yt_version))

        yt_version: Dict[str, Dict[str, str]] = {
            "Original_APK": {
                "Name": f"com.google.android.youtube.{latest_yt_version}.apk",
                "Version": latest_yt_version
            }
        }

        write_new_versions_and_names(latest_versions=yt_version,
                                     config_path=config_path,
                                     config_data=get_config(config_path=config_path))
    elif not force_patch:
        logging.log(MESSAGE, "No new YT version is supported, latest version has been already patched.")
        sys.exit(0)
    else:
        logging.log(MESSAGE, "No new YT version is supported but --force-patch was used.")

    patch_latest_yt_apk(config_path=config_path)

    logging.log(MESSAGE, "All done.")


def get_config(config_path: str) -> Dict[str, str | Dict[str, str]]:
    with open(config_path, "r", encoding="utf-8", errors="backslashreplace") as stream:
        return copy.deepcopy(json.loads(stream.read()))


def get_latest_versions(include_beta: bool) -> Dict[str, Dict[str, Optional[str]]]:
    latest_cli: Dict[str, Optional[str]] = get_latest_cli(include_beta=include_beta)
    latest_patch_bundle: Dict[str, Optional[str]] = get_latest_patch_bundle(include_beta=include_beta)

    return {
        "CLI": latest_cli,
        "Patches": latest_patch_bundle,
    }


def get_latest_cli(include_beta: bool) -> Dict[str, Optional[str]]:
    url: str = "https://api.github.com/repos/revanced/revanced-cli/releases"

    newest_version: str
    newest_version_url: Optional[str]
    newest_version_name: Optional[str]
    newest_version, newest_version_url, newest_version_name = get_latest_version_name_and_url(url=url,
                                                                                              ext="jar",
                                                                                              tool_name="cli",
                                                                                              include_beta=include_beta)

    return {
        "Name": newest_version_name,
        "Version": newest_version,
        "URL": newest_version_url
    }


def get_latest_patch_bundle(include_beta: bool) -> Dict[str, Optional[str]]:
    url: str = "https://api.github.com/repos/revanced/revanced-patches/releases"

    newest_version: str
    newest_version_url: Optional[str]
    newest_version_name: Optional[str]
    newest_version, newest_version_url, newest_version_name = get_latest_version_name_and_url(url=url,
                                                                                              ext="rvp",
                                                                                              tool_name="patch bundle",
                                                                                              include_beta=include_beta)

    return {
        "Name": newest_version_name,
        "Version": newest_version,
        "URL": newest_version_url
    }


def get_latest_version_name_and_url(url: str,
                                    ext: str,
                                    tool_name: str,
                                    include_beta: bool) -> tuple[str, Optional[str], Optional[str]]:

    sess: Session = requests.session()
    sess.headers.update({
        "User-Agent": USER_AGENT
    })

    resp: Response = sess.get(url, allow_redirects=True)

    if not 199 < resp.status_code < 300:
        logging.critical(f"There was an error fetching the latest version for \"{tool_name}\": {resp.status_code}")
        sys.exit(1)

    json_data: dict = json.loads(resp.content)

    if include_beta:
        newest_version = json_data[0]["tag_name"]
        newest_version_url, newest_version_name = get_download_url(json_data[0], ext, tool_name)
    else:
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


def get_latest_supported_yt_version(config_path: str) -> str:
    config_data: Dict[str, str | Dict[str, str]] = get_config(config_path=config_path)

    get_supported_versions_cmd = [
        "java",
        "-jar",
        os.path.join(config_data["Store_Path"], config_data["Names"]["CLI"]),
        "list-versions",
        os.path.join(config_data["Store_Path"], config_data["Names"]["Patches"]),
        "-f",
        "com.google.android.youtube"
    ]

    try:
        list_versions_output = subprocess.check_output(get_supported_versions_cmd, encoding="utf-8")
    except subprocess.CalledProcessError as e:
        logging.critical(f"Couldn't get latest versions, error: {e}", exc_info=True)
        exit(1)

    supported_versions = extract_versions(output=list_versions_output)

    if len(supported_versions) == 0:
        logging.critical("No supported version found for Youtube. "
                         "Unless something has changed since this program has been written, this shouldn't happen.")
        sys.exit(2)

    if len(supported_versions) == 1:
        return supported_versions[0]
    else:
        newest_found: str = ""
        for version_to_check in supported_versions:
            if compare_versions(version_to_check=version_to_check,
                                latest_version_found=newest_found):
                newest_found = version_to_check

        return newest_found

def extract_versions(output: str) -> List[str]:
    return re.findall(r"(?:\s|^|\t)+([0-9]+\.[0-9.]+)", output)

def compare_versions(version_to_check: str,
                     latest_version_found: str) -> bool:
    if version_to_check == "" or latest_version_found == "":
        return True

    version_to_check: str = re.sub(VERSION_REGEX, "", version_to_check)
    latest_version_found: str = re.sub(VERSION_REGEX, "", latest_version_found)

    version_to_check_list: List[str] = version_to_check.split(".")
    latest_version_found_list: List[str] = latest_version_found.split(".")

    if len(version_to_check_list) > len(latest_version_found_list):
        return True
    else:
        # if they have equal length, this will still return the correct number
        min_number: int = min(len(version_to_check_list), len(latest_version_found_list)) - 1

        return compare_version_numbers(version_to_check=version_to_check_list, latest_version_found=latest_version_found_list, number=min_number)


def compare_version_numbers(version_to_check: list,
                            latest_version_found: list,
                            number: int) -> bool:
    """
    Should return True if "version_to_check" is higher than "latest_version_found".
    :param version_to_check: The version to check against
    :param latest_version_found: The newest version found so far
    :param number: Number of items in the list that will be iterated
    :return: Whether the "version_to_check" is higher than the "latest_version_found"
    """
    for i in range(number):
        version_to_check_fr: str = version_to_check[i]
        latest_version_found_fr: str = latest_version_found[i]

        pad: int = max(len(version_to_check_fr), len(latest_version_found_fr)) - 1

        version_to_check_fr = end_fill(version_to_check_fr, pad)
        latest_version_found_fr = end_fill(latest_version_found_fr, pad)

        if int(version_to_check_fr) > int(latest_version_found_fr):
            return True
        elif int(version_to_check_fr) < int(latest_version_found_fr):
            return False
        else:
            continue

    return False


def write_new_versions_and_names(latest_versions: Dict[str, Dict[str, Optional[str]]],
                                 config_path: str,
                                 config_data: Dict[str, str | Dict[str, str]]) -> None:
    new_config: Dict[str, str | Dict[str, str | Dict[str, str | bool]]] = copy.deepcopy(config_data)

    for tool in latest_versions.keys():
        new_config["Versions"][tool] = latest_versions[tool]["Version"]
        new_config["Names"][tool] = sanitize_name(latest_versions[tool]["Name"])

    with open(config_path, "w", encoding="utf-8") as stream:
        json.dump(new_config, stream, indent=4)


def end_fill(string: str,
             amount: int) -> str:
    if len(string) >= amount:
        return string
    else:
        return string + "0" * (amount - len(string))


async def download_latest_yt_apk(config_path: str,
                                 version: str) -> None:
    url: str = f"https://apkpure.com/youtube/com.google.android.youtube/download/{version}"
    config_data: Dict[str, str | Dict[str, str]] = get_config(config_path=config_path)
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
                await page.click("a.jump-downloading-btn", button="left", timeout=180000)
            download = await downloader.value
            await download.save_as(os.path.join(config_data["Store_Path"], f"com.google.android.youtube.{version}.apk"))
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


def patch_latest_yt_apk(config_path: str) -> None:
    logging.log(MESSAGE, "Patching latest YouTube APK...")
    config: Dict[str, str | Dict[str, str]] = get_config(config_path=config_path)

    cli_path: str = os.path.join(config["Store_Path"], config["Names"]["CLI"])
    patches_path: str = os.path.join(config["Store_Path"], config["Names"]["Patches"])
    output_file: str = os.path.join(config["Output"],
                                    f"app.revanced.android.youtube.{config['Versions']['Original_APK']}.apk")
    input_file: str = os.path.join(config["Store_Path"], config["Names"]["Original_APK"])

    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    try:
        os.remove(output_file)
    except FileNotFoundError:
        pass

    command = ["java",
               "-jar",
               cli_path,
               "patch",
               "--purge",
               "--patches",
               patches_path,
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
