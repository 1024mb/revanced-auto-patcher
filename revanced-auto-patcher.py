import argparse
import copy
import html
import json
import os
import re
import subprocess
import sys
import urllib.request
from urllib.error import HTTPError

import bs4
import orjson
import playwright.sync_api
import pydantic
import requests
from bs4 import Tag
from internetarchive import files, get_files
from loguru import logger
from playwright.sync_api import sync_playwright, Browser
from requests import Session, Response

from __init__ import __version__

if sys.version_info < (3, 10):
    print("Python 3.10 or higher is required.")
    sys.exit(1)

USER_AGENT: str = (r"Mozilla/5.0 (iPhone; CPU iPhone OS 14_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                   r"FxiOS/127.0 Mobile/15E148 Safari/605.1.15")
PLATFORM: str = sys.platform
VERSION_REGEX: re.Pattern[str] = re.compile(r"^v\.?", flags=re.IGNORECASE)

PREMIUM_APPS: tuple[str, ...] = (
    "com.andrewshu.android.redditdonation",
    "com.onelouder.baconreader.premium",
)


class ToolAppData(pydantic.BaseModel):
    version: str
    filename: str


class AppData(pydantic.BaseModel):
    version: str
    filename: dict[str, str]


class Config(pydantic.BaseModel):
    Output: str
    Store_Path: str
    ArchiveOrg_Collection: str | None
    Tools: dict[str, ToolAppData]
    Apps: dict[str, AppData]
    Name_Relations: dict[str, str]


class OldConfig(pydantic.BaseModel):
    Output: str
    Store_Path: str
    Versions: dict[str, str]
    Names: dict[str, str]


session: Session = requests.session()
session.headers.update({
    "User-Agent": USER_AGENT
})

playwright_instance: Browser

ABIS: list[str] = [
    "all",
    "any",
    "x86",
    "x32",
    "x86-64",
    "x86_64",
    "x64",
    "armeabi-v7a",
    "armeabi",
    "arm7",
    "arm64-v8a",
    "aarch64",
    "arm8",
]

REAL_ABIS: list[str] = [
    "x86",
    "x86_64",
    "armeabi-v7a",
    "arm64-v8a",
]

ARCHIVE_ORG_AVAILABLE_FILES: list[files.File] = []

compatible_packages: list[str] = []


def main():
    parser = argparse.ArgumentParser(prog="revanced-auto-patcher",
                                     description="Automatically download and patch latest supported "
                                                 "YouTube Android app with ReVanced.")
    parser.add_argument("-v", "--version",
                        action="version",
                        version=f"%(prog)s v{__version__}")
    parser.add_argument("packages",
                        nargs="*",
                        help="Apps to download and patch")
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
    parser.add_argument("--include-abi",
                        help="Architectures to include for the package(s).",
                        choices=ABIS,
                        nargs="*",
                        default=["all"])
    parser.add_argument("--skip-previous-apps",
                        help="Skip processing previously processed apps (registered in the config file).",
                        action="store_true")
    parser.add_argument("--no-archive-org",
                        help="Do not use archive.org for downloading the latest version.",
                        action="store_true")
    parser.add_argument("--archive-org-identifier",
                        help="Collection's identifier to use to lookup for APK files in archive.org.",
                        type=str,
                        required=False)
    # noinspection PyTypeChecker
    parser.add_argument("--log-level",
                        help="How much stuff is logged.",
                        default="INFO",
                        choices=["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"],
                        type=str.upper)

    args = parser.parse_args()

    config_path: str = args.conf[0]
    output: str = args.output[0]
    store_path: str = args.store_path[0]
    include_beta: bool = args.include_beta
    force_patch: bool = args.force_patch
    packages: list[str] = args.packages
    no_archive_org: bool = args.no_archive_org
    abis: list[str] = []
    skip_previous_apps: bool = args.skip_previous_apps
    archive_org_identifier: str | None = args.archive_org_identifier

    for abi in args.include_abi:
        orig_abi = abi
        abi = abi.lower()
        if abi in ("armeabi-v7a", "armeabi", "arm7"):
            abis.append("armeabi-v7a")
        elif abi in ("x64", "x86-64", "x86_64"):
            abis.append("x86_64")
        elif abi in ("x86", "x32"):
            abis.append("x86")
        elif abi in ("arm64-v8a", "aarch64", "arm8"):
            abis.append("arm64-v8a")
        elif abi == "all" or abi == "any":
            abis = copy.copy(REAL_ABIS)
            break
        else:
            raise ValueError(f"Unknown abi: {orig_abi}")

    logger_format: str = ("<green>{time:YYYY-MM-DD HH:mm:ss.SSS Z}</green> | <level>{level: <8}</level> | "
                          "<level>{message}</level>")
    logger.remove()
    if args.log_level in ("DEBUG", "TRACE"):
        logger.add(sys.stderr, level=args.log_level)
    else:
        logger.add(sys.stderr, level=args.log_level, format=logger_format)

    if not os.path.exists(config_path):
        logger.info("Initializing configuration...")
        init_(conf=config_path,
              output=output,
              store_path=store_path,
              archive_org_identifier=archive_org_identifier)

    install_playwright()

    start_process(config_path=config_path,
                  include_beta=include_beta,
                  force_patch=force_patch,
                  packages=packages,
                  abis=abis,
                  no_archive_org=no_archive_org,
                  skip_previous_apps=skip_previous_apps,
                  archive_org_identifier=archive_org_identifier)


def init_(conf: str,
          output: str,
          store_path: str,
          archive_org_identifier: str | None) -> None:
    config: Config = Config(Output=output,
                            Store_Path=store_path,
                            ArchiveOrg_Collection=archive_org_identifier,
                            Tools={
                                "CLI": ToolAppData(version="", filename=""),
                                "Patches": ToolAppData(version="", filename=""),
                                "APKEditor": ToolAppData(version="", filename=""),
                            },
                            Apps={},
                            Name_Relations={})

    write_config_file(data=config, filepath=conf)


def start_process(config_path: str,
                  include_beta: bool,
                  force_patch: bool,
                  packages: list[str],
                  abis: list[str],
                  no_archive_org: bool,
                  skip_previous_apps: bool,
                  archive_org_identifier: str | None) -> None:
    logger.info("Starting up...")

    if is_old_config(config_path=config_path):
        migrate_config(config_path=config_path, archive_org_identifier=archive_org_identifier)

    config_data: Config = get_config(config_path=config_path)
    latest_versions: dict[str, dict[str, str | None]] = get_latest_versions(include_beta=include_beta)

    if archive_org_identifier is None:
        archive_org_identifier = config_data.ArchiveOrg_Collection
    else:
        if config_data.ArchiveOrg_Collection is not None:
            logger.info(f"Replacing archive.org identifier from \"{config_data.ArchiveOrg_Collection}\" to \""
                        f"{archive_org_identifier}\".")
        config_data.ArchiveOrg_Collection = archive_org_identifier

    logger.info("Checking tools...")

    new_ver_available: bool = False
    patches_new_version: bool = False

    for tool in config_data.Tools:
        if latest_versions[tool]["URL"] is None or latest_versions[tool]["Name"] is None:
            continue

        if latest_versions[tool]["Version"] != config_data.Tools[tool].version:
            logger.info(f"New version found for {tool}: {latest_versions[tool]['Version']}.")
            new_ver_available = True
            if tool == "Patches":
                patches_new_version = True

            download_latest_version(url=latest_versions[tool]["URL"],
                                    name=latest_versions[tool]["Name"],
                                    download_path=config_data.Store_Path)

            if config_data.Tools[tool].filename != "":
                old_version_path = os.path.join(config_data.Store_Path, config_data.Tools[tool].filename)

                try:
                    logger.info(f"Removing old version of {tool}...")
                    os.remove(old_version_path)
                except FileNotFoundError:
                    pass
                except PermissionError:
                    logger.warning(f"There was an error removing the file: {old_version_path}")
        else:
            logger.info(f"{tool} is already updated to the latest version.")

    setup_playwright()

    if new_ver_available:
        config_data = write_new_versions_and_names(latest_versions=latest_versions,
                                                   config_path=config_path,
                                                   config_data=config_data)

    if patches_new_version:
        config_data = write_package_name_relations(config_path=config_path,
                                                   config_data=config_data)

    if len(config_data.Name_Relations) == 0:
        config_data = write_package_name_relations(config_path=config_path,
                                                   config_data=config_data)

    package_names: set[str] = set()
    cli_path = os.path.join(config_data.Store_Path, config_data.Tools["CLI"].filename)
    patches_path = os.path.join(config_data.Store_Path, config_data.Tools["Patches"].filename)

    get_compatible_packages(cli_path=cli_path,
                            patches_path=patches_path)

    if len(packages) == 0:
        packages = compatible_packages.copy()
        for premium_app in PREMIUM_APPS:
            try:
                packages.remove(premium_app)
            except KeyError:
                pass

    for package in packages:
        package_name = config_data.Name_Relations.get(package.strip(),
                                                      config_data.Name_Relations.get(package.strip().lower(),
                                                                                     package.strip()))
        if package_name in PREMIUM_APPS:
            continue

        if package_name not in package_names:
            if package_name in compatible_packages:
                package_names.add(package_name)
            else:
                logger.warning(f"{package_name} is not compatible with ReVanced or app name is unknown.")

    if not skip_previous_apps:
        for package_name in copy.copy(config_data.Apps):
            if package_name not in package_names:
                if package_name in compatible_packages:
                    package_names.add(package_name)
                else:
                    logger.warning(f"{package_name} is not compatible with ReVanced anymore.")
                    config_data.Apps.pop(package_name)

    for package_name in package_names:
        logger.info(f"Processing {package_name}...")
        config_data = process_package(package_name=package_name,
                                      config_data=config_data,
                                      config_path=config_path,
                                      force_patch=force_patch,
                                      abis=abis,
                                      no_archive_org=no_archive_org,
                                      archive_org_identifier=archive_org_identifier)

    write_config_file(data=config_data, filepath=config_path)

    logger.info("All done.")


def get_config(config_path: str) -> Config:
    with open(config_path, "rb") as stream:
        return Config.model_validate_json(stream.read(), strict=True)


def get_latest_versions(include_beta: bool) -> dict[str, dict[str, str | None]]:
    latest_cli: dict[str, str | None] = get_latest_cli(include_beta=include_beta)
    latest_patch_bundle: dict[str, str | None] = get_latest_patch_bundle(include_beta=include_beta)
    latest_apkeditor: dict[str, str | None] = get_latest_apkeditor(include_beta=include_beta)

    return {
        "CLI": latest_cli,
        "Patches": latest_patch_bundle,
        "APKEditor": latest_apkeditor,
    }


def get_latest_cli(include_beta: bool) -> dict[str, str | None]:
    url: str = "https://api.github.com/repos/revanced/revanced-cli/releases"

    newest_version: str
    newest_version_url: str | None
    newest_version_name: str | None
    newest_version, newest_version_url, newest_version_name = get_latest_version_name_and_url(url=url,
                                                                                              ext="jar",
                                                                                              tool_name="cli",
                                                                                              include_beta=include_beta)

    return {
        "Name": newest_version_name,
        "Version": newest_version,
        "URL": newest_version_url
    }


def get_latest_patch_bundle(include_beta: bool) -> dict[str, str | None]:
    url: str = "https://api.github.com/repos/revanced/revanced-patches/releases"

    newest_version: str
    newest_version_url: str | None
    newest_version_name: str | None
    newest_version, newest_version_url, newest_version_name = get_latest_version_name_and_url(url=url,
                                                                                              ext="rvp",
                                                                                              tool_name="patch bundle",
                                                                                              include_beta=include_beta)

    return {
        "Name": newest_version_name,
        "Version": newest_version,
        "URL": newest_version_url
    }


def get_latest_apkeditor(include_beta: bool) -> dict[str, str | None]:
    url: str = "https://api.github.com/repos/REAndroid/APKEditor/releases"

    newest_version: str
    newest_version_url: str | None
    newest_version_name: str | None
    newest_version, newest_version_url, newest_version_name = get_latest_version_name_and_url(url=url,
                                                                                              ext="jar",
                                                                                              tool_name="apkeditor",
                                                                                              include_beta=include_beta)

    return {
        "Name": newest_version_name,
        "Version": newest_version,
        "URL": newest_version_url
    }


def get_latest_version_name_and_url(url: str,
                                    ext: str,
                                    tool_name: str,
                                    include_beta: bool) -> tuple[str, str | None, str | None]:
    resp: Response = session.get(url, allow_redirects=True)
    resp.raise_for_status()
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

    return VERSION_REGEX.sub("", newest_version), newest_version_url, newest_version_name,


def get_download_url(data: dict,
                     extension: str,
                     tool_name: str) -> tuple[str | None, str | None]:
    item_number: int = len(data["assets"])

    if item_number == 0:
        logger.warning(f"No assets found for \"{tool_name}\"")
        return None, None,

    i: int
    for i in range(0, item_number):
        if data["assets"][i]["name"].lower().endswith(extension):
            return data["assets"][i]["browser_download_url"], data["assets"][i]["name"],

    logger.warning(f"No matching assets found for \"{tool_name}\" [expected extension: {extension}]")
    return None, None,


def download_latest_version(url: str,
                            name: str,
                            download_path: str) -> None:
    logger.info(f"Downloading {name}...")

    name: str = sanitize_name(name)
    os.makedirs(download_path, exist_ok=True)

    response: Response = session.get(url, stream=True)

    try:
        with open(os.path.join(download_path, name), "wb", buffering=0) as stream:
            chunk: bytes
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    stream.write(chunk)
    except OSError as e:
        logger.critical(f"There was an error downloading {name}: {e}", exc_info=True)
        sys.exit(1)

    logger.info("Finished downloading.")


def sanitize_name(name: str,
                  do_not_use_unicode: bool = False) -> str:
    illegal_characters: dict[str, str] = {}

    if PLATFORM == "win32" or PLATFORM == "msys" or PLATFORM == "cygwin":
        illegal_characters = {
            "<": "_" if do_not_use_unicode else "\uFE64",
            ">": "_" if do_not_use_unicode else "\uFE65",
            ":": "_" if do_not_use_unicode else "\uFE55",
            "\"": "_" if do_not_use_unicode else "\uFF02",
            "/": "_" if do_not_use_unicode else "\uFF0F",
            "\\": "_" if do_not_use_unicode else "\uFF3C",
            "|": "_" if do_not_use_unicode else "\uFF5C",
            "?": "_" if do_not_use_unicode else "\uFF1F",
            "*": "_" if do_not_use_unicode else "\uFF0A"
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


def get_latest_supported_version(package_name: str,
                                 config_path: str) -> str:
    config_data: Config = get_config(config_path=config_path)

    get_supported_versions_cmd = [
        "java",
        "-jar",
        os.path.join(config_data.Store_Path, config_data.Tools["CLI"].filename),
        "list-versions",
        os.path.join(config_data.Store_Path, config_data.Tools["Patches"].filename),
        "-f",
        package_name
    ]

    try:
        list_versions_output = subprocess.check_output(get_supported_versions_cmd, encoding="utf-8").splitlines()
    except subprocess.CalledProcessError as e:
        logger.opt(exception=True).critical(f"Couldn't get latest supported versions for {package_name}, error: {e}")
        sys.exit(1)

    supported_versions: list[str] | str = extract_versions(output=list_versions_output)

    if supported_versions == "any":
        return "any"

    if len(supported_versions) == 0:
        logger.critical(f"No supported version found for {package_name}. "
                        f"Unless something has changed since this program has been written, this shouldn't happen.")
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


def extract_versions(output: list[str]) -> list[str] | str:
    versions: list[str] = []

    for line in output:
        if "any" == line.strip().lower():
            return "any"

        try:
            version = re.search(r"(?:\s+|^)((?:v|v\.)?\d+\.\S+)", line.strip()).group(1)
            versions.append(version)
        except (IndexError, AttributeError, KeyError):
            continue

    return versions


def compare_versions(version_to_check: str,
                     latest_version_found: str,
                     thorough: bool = True) -> bool:
    if not thorough:
        if version_to_check == "" or latest_version_found == "":
            return True

    if "any" == version_to_check:
        return True

    if version_to_check == latest_version_found:
        return False

    version_to_check: str = VERSION_REGEX.sub("", version_to_check)
    latest_version_found: str = VERSION_REGEX.sub("", latest_version_found)

    if re.search(r"[^\d.]", version_to_check) is not None:
        version_to_check = get_sanitized_version(version=version_to_check)

    if re.search(r"[^\d.]", latest_version_found) is not None:
        latest_version_found = get_sanitized_version(version=latest_version_found)

    version_to_check_list: list[str] = version_to_check.split(".")
    latest_version_found_list: list[str] = latest_version_found.split(".")

    if len(version_to_check_list) > len(latest_version_found_list):
        return True
    else:
        # if they have equal length, this will still return the correct number
        min_number: int = min(len(version_to_check_list), len(latest_version_found_list)) - 1

        return compare_version_numbers(version_to_check=version_to_check_list,
                                       latest_version_found=latest_version_found_list,
                                       number=min_number)


def get_sanitized_version(version: str) -> str:
    letter_version: dict[str, str] = {
        "a": "01",
        "b": "02",
        "c": "03",
        "d": "04",
        "e": "05",
        "f": "06",
        "g": "07",
        "h": "08",
        "i": "09",
        "j": "10",
        "k": "11",
        "l": "12",
        "m": "13",
        "n": "14",
        "o": "15",
        "p": "16",
        "q": "17",
        "r": "18",
        "s": "19",
        "t": "20",
        "u": "21",
        "v": "22",
        "w": "23",
        "x": "24",
        "y": "25",
        "z": "26"
    }

    version_split: list[str] = version.split(".")
    for main_idx, part in enumerate(version_split.copy()):
        if not part.isnumeric():
            part_list: list[str] = list(part)
            text_char_found: int = 0
            for idx, char in enumerate(part_list.copy()):
                if char.lower() in letter_version:
                    text_char_found += 1
                    part_list[idx] = letter_version[char.lower()]
                elif not char.isnumeric():
                    part_list[idx] = ""

            if text_char_found > 1:
                part = re.sub("[a-zA-ZÁ-Úá-ú]+", "", part)
                part_list: list[str] = list(part)
                for idx, char in enumerate(part_list.copy()):
                    if char.lower() in letter_version:
                        text_char_found += 1
                        part_list[idx] = letter_version[char.lower()]
                    elif not char.isnumeric():
                        part_list[idx] = ""

            version_split[main_idx] = "".join(part_list)

    for idx, part in enumerate(version_split.copy()):
        if part == "":
            version_split.pop(idx)

    return ".".join(version_split)


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


def write_new_versions_and_names(latest_versions: dict[str, dict[str, str | None]],
                                 config_path: str,
                                 config_data: Config) -> Config:
    for tool in latest_versions:
        try:
            config_data.Tools[tool].version = latest_versions[tool]["Version"]
            config_data.Tools[tool].filename = sanitize_name(latest_versions[tool]["Name"])
        except KeyError:
            config_data.Tools[tool] = ToolAppData(version=latest_versions[tool]["Version"],
                                                  filename=sanitize_name(latest_versions[tool]["Name"]))

    config_data = Config.model_validate(config_data, strict=True)

    write_config_file(filepath=config_path,
                      data=config_data)

    return config_data


def end_fill(string: str,
             amount: int) -> str:
    if len(string) >= amount:
        return string
    else:
        return string + "0" * (amount - len(string))


def download_latest_apk(config_data: Config,
                        package_name: str,
                        version: str,
                        abis: list[str],
                        no_archive_org: bool,
                        archive_org_identifier: str | None) -> dict[str, str] | None:
    """
    This should return a dict with the keys being the file names and the values the ABI they correspond to.
    """
    file_names = download_with_apkpure(config_data=config_data,
                                       package_name=package_name,
                                       version=version,
                                       abis=abis)

    if file_names is None:
        if no_archive_org or archive_org_identifier is None:
            return None

        logger.info("Trying with archive.org...")

        file_names = retrieve_from_archive_org(config_data=config_data,
                                               package_name=package_name,
                                               version=version,
                                               abis=abis,
                                               archive_org_identifier=archive_org_identifier)

    return file_names


def download_with_apkpure(config_data: Config,
                          package_name: str,
                          version: str,
                          abis: list[str]) -> dict[str, str] | None:
    url: str = f"https://apkpure.com/xxxxxx/{package_name}/download/{version}"
    file_names: dict[str, str] = {}

    logger.info(f"Downloading latest APK for {package_name}...")

    with playwright_instance.new_page(user_agent=USER_AGENT) as page:
        try:
            resp = page.goto(url)

            if resp.url.startswith("https://apkpure.com/apk-downloader"):
                logger.warning(f"Version {version} not found for {package_name} in APKPure.")
                return None

            if not resp.ok:
                logger.error(f"There was an error retrieving the app page. HTTP error: {resp.status}")
                return None

            page.wait_for_load_state("load")

            bs = bs4.BeautifulSoup(page.content(), "html.parser")

            found_divs = bs.find_all("div",
                                     {
                                         "class": "group-title"
                                     })

            div_to_search: dict[str, Tag] = {}

            for div in found_divs:
                if div.text in abis:
                    next_sibling = div.next_sibling
                    if next_sibling is not None:
                        div_to_search[div.text] = next_sibling

            urls_to_download: dict[str, str] = {}

            for abi in div_to_search:
                a_section = div_to_search[abi].find("a",
                                                    {
                                                        "class": "download-btn"
                                                    })

                if a_section is None:
                    continue

                urls_to_download[abi] = a_section["href"]

            for abi, extracted_url in urls_to_download.items():
                with page.expect_download() as downloader:
                    page.click(f"a[href=\"{extracted_url}\"]")

                download = downloader.value

                match os.path.splitext(download.suggested_filename)[1].lower():
                    case ".xapk":
                        file_ext = "xapk"
                    case ".apk":
                        file_ext = "apk"
                    case unknown:
                        raise ValueError(f"Unexpected file extension: {unknown}")

                file_name: str = os.path.join(config_data.Store_Path,
                                              sanitize_name(name=f"{package_name}.{version}.{abi}.{file_ext}",
                                                            do_not_use_unicode=True))
                file_names[file_name] = abi

                download.save_as(file_name)
        except playwright.sync_api.TimeoutError as e:
            logger.opt(exception=True).critical(f"Timeout error: {e}")
            return None
        except playwright.sync_api.Error as e:
            logger.opt(exception=True).critical(f"Playwright error: {e}")
            sys.exit(1)
        except OSError as e:
            logger.opt(exception=True).error(f"There was an error saving the APK file: {e}")
            sys.exit(1)

        logger.info("Finished downloading.")
        return file_names


def retrieve_from_archive_org(config_data: Config,
                              package_name: str,
                              version: str,
                              abis: list[str],
                              archive_org_identifier: str) -> dict[str, str] | None:
    if len(ARCHIVE_ORG_AVAILABLE_FILES) == 0:
        get_available_files(archive_org_identifier=archive_org_identifier)

    tried_universal: bool = False

    if len(abis) == 4:
        file_names = download_from_archive_org_universal(config_data=config_data,
                                                         package_name=package_name,
                                                         version=version)
        tried_universal = True

        if file_names is not None:
            return file_names

    matched, file_names = download_from_archive_org_individual(config_data=config_data,
                                                               package_name=package_name,
                                                               version=version,
                                                               abis=abis)

    if not tried_universal and (not matched or file_names is None):
        return download_from_archive_org_universal(config_data=config_data,
                                                   package_name=package_name,
                                                   version=version)
    else:
        return file_names


def download_from_archive_org_individual(config_data: Config,
                                         package_name: str,
                                         version: str,
                                         abis: list[str]) -> tuple[bool, dict[str, str] | None]:
    file_names: dict[str, str] | None = {}
    matched: bool = False

    for abi in abis:
        success: bool = False
        found: bool = False
        name = f"{package_name}.{version}.{abi}.apk"
        file_name: str = sanitize_name(name, do_not_use_unicode=True)

        for file in ARCHIVE_ORG_AVAILABLE_FILES:
            if file.name == name:
                logger.info(f"Downloading {package_name} with ABI {abi} as \"{name}\"...")
                found = True
                success = file.download(file_path=file_name,
                                        destdir=config_data.Store_Path,
                                        ignore_existing=True,
                                        checksum=True,
                                        retries=10)

                if not success:
                    # try one more time
                    success = file.download(file_path=file_name,
                                            destdir=config_data.Store_Path,
                                            ignore_existing=True,
                                            checksum=True,
                                            retries=10)
                break

        if found:
            if success:
                logger.info(f"Finished downloading \"{name}\".")
                matched = True
                file_names[file_name] = abi
            else:
                logger.error(f"Failed download for {package_name} with ABI: {abi}.")
        else:
            logger.warning(f"No matching APK found for {package_name} with ABI: {abi} in archive.org.")

    if not matched:
        file_names = None

    return matched, file_names


def download_from_archive_org_universal(config_data: Config,
                                        package_name: str,
                                        version: str) -> dict[str, str] | None:
    name: str = f"{package_name}.{version}.apk"
    file_name: str = sanitize_name(f"{package_name}.{version}.apk", do_not_use_unicode=True)
    success: bool = False
    found: bool = False

    for file in ARCHIVE_ORG_AVAILABLE_FILES:
        if file.name == name:
            found = True
            logger.info(f"Downloading multi-arch package of {package_name} as \"{name}\"...")
            success = file.download(file_path=file_name,
                                    destdir=config_data.Store_Path,
                                    ignore_existing=True,
                                    retries=10)
            break

    if not found:
        logger.info(f"Multi-arch APK \"{name}\" not found on archive.org")
        return None
    elif not success:
        logger.error(f"Failed download for multi-arch package of {package_name}.")
        return None
    else:
        logger.info(f"Finished downloading multi-arch package of {package_name}.")
        return {
            file_name: "any"
        }


def patch_apk(config_data: Config,
              package_name: str) -> None:
    logger.info(f"Patching latest {package_name} APK...")

    cli_path: str = os.path.join(config_data.Store_Path, config_data.Tools["CLI"].filename)
    patches_path: str = os.path.join(config_data.Store_Path, config_data.Tools["Patches"].filename)

    if "com.google" in package_name:
        new_package_name = package_name.replace("com.google", "app.revanced")
    else:
        new_package_name = package_name.replace(package_name.split(".")[0] + ".", "app.revanced.", 1)

    for input_filename, abi in config_data.Apps[package_name].filename.items():
        if abi == "any":
            output_name = f"{new_package_name}.{config_data.Apps[package_name].version}.apk"
        else:
            output_name = f"{new_package_name}.{config_data.Apps[package_name].version}.{abi}.apk"

        output_file: str = os.path.join(config_data.Output, sanitize_name(output_name, do_not_use_unicode=True))

        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        try:
            os.remove(output_file)
        except FileNotFoundError:
            pass

        command = [
            "java",
            "-jar",
            cli_path,
            "patch",
            "--purge",
            "--patches",
            patches_path,
            "--out",
            output_file,
            os.path.join(config_data.Store_Path, input_filename)
        ]

        try:
            subprocess.run(command, encoding="utf-8", check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            logger.opt(exception=True).critical(f"Error patching APK:\nSTDOUT: {e.stdout}\nSTDERR: {e.stderr}")
            sys.exit(1)

    logger.info("Patching finished.")


def install_playwright() -> None:
    if "__compiled__" in globals():
        # nuitka bundles the browser already
        return

    cmd: list[str] = [
        "playwright",
        "install",
        "firefox"
    ]

    try:
        subprocess.run(cmd, encoding="utf-8", check=True)
    except subprocess.CalledProcessError:
        logger.opt(exception=True).critical("There was an error installing playwright:")
        sys.exit(3)


def setup_playwright():
    global playwright_instance
    pw = sync_playwright().start()
    playwright_instance = pw.firefox.launch(headless=True)


def get_compatible_packages(cli_path: str,
                            patches_path: str,
                            lower: bool = False) -> list[str]:
    global compatible_packages

    if len(compatible_packages) > 0:
        return compatible_packages

    command = [
        "java",
        "-jar",
        cli_path,
        "list-versions",
        patches_path
    ]

    output = subprocess.check_output(command, encoding="utf-8")
    output = output.splitlines()

    for line in output:
        try:
            extracted_package = re.search(r"Package\s+name:\s+(\S+)+", line).group(1)
            if lower:
                compatible_packages.append(extracted_package.lower())
            else:
                compatible_packages.append(extracted_package)
        except (IndexError, AttributeError, KeyError):
            continue

    return compatible_packages


def write_config_file(filepath: str,
                      data: Config) -> None:
    with open(filepath, "wb") as stream:
        stream.write(orjson.dumps(data.model_dump(), option=orjson.OPT_INDENT_2))


def write_package_name_relations(config_path: str,
                                 config_data: Config) -> Config:
    logger.info("Writing package name relations...")

    cli_path = os.path.join(config_data.Store_Path, config_data.Tools["CLI"].filename)
    patches_path = os.path.join(config_data.Store_Path, config_data.Tools["Patches"].filename)

    get_compatible_packages(cli_path=cli_path,
                            patches_path=patches_path)

    name_relations = copy.copy(config_data.Name_Relations)
    for package in compatible_packages:
        found = False
        for app_name in config_data.Name_Relations:
            if package == config_data.Name_Relations[app_name]:
                found = True
                break
        if not found:
            app_name = get_app_name(package_name=package).lower()
            if app_name != "":
                name_relations[app_name] = package

    config_data.Name_Relations = name_relations
    config_data = Config.model_validate(config_data, strict=True)

    write_config_file(filepath=config_path,
                      data=config_data)

    logger.info("Finished writing package name relations.")

    return config_data


def get_app_name(package_name: str) -> str:
    store_page = get_play_store_page(package_name=package_name)

    try:
        package_name = html.unescape(re.search(r'itemprop="name">(.+?)(?:</span>)?</h1>', store_page).group(1)).strip()
    except (IndexError, AttributeError, KeyError):
        package_name = ""

    return package_name


def get_play_store_page(package_name: str) -> str:
    playstore_url = "https://play.google.com/store/apps/details?id="

    playstore_url_comp = playstore_url + package_name + "&hl=en-US"

    try:
        response = urllib.request.urlopen(playstore_url_comp).read().decode()
    except HTTPError:
        return ""

    if ">We're sorry, the requested URL was not found on this server.</div>" in response:
        return ""

    return response


def process_package(package_name: str,
                    config_data: Config,
                    config_path: str,
                    force_patch: bool,
                    abis: list[str],
                    no_archive_org: bool,
                    archive_org_identifier: str | None) -> Config:
    if package_name.lower() not in get_compatible_packages(
            cli_path=os.path.join(config_data.Store_Path, config_data.Tools["CLI"].filename),
            patches_path=os.path.join(config_data.Store_Path, config_data.Tools["Patches"].filename),
            lower=True
    ):
        logger.error(f"{package_name} is not a compatible package.")
        return config_data

    try:
        current_version: str = config_data.Apps[package_name].version
    except (IndexError, AttributeError, KeyError):
        current_version = ""

    latest_version: str = get_latest_supported_version(package_name=package_name,
                                                       config_path=config_path)

    if latest_version == "any":
        latest_version = search_latest_version(package_name=package_name)

    if latest_version is None:
        return config_data

    if compare_versions(version_to_check=latest_version, latest_version_found=current_version, thorough=False):
        logger.info(f"New version supported for {package_name}...")

        file_names: dict[str, str] | None = download_latest_apk(config_data=config_data,
                                                                package_name=package_name,
                                                                version=latest_version,
                                                                abis=abis,
                                                                no_archive_org=no_archive_org,
                                                                archive_org_identifier=archive_org_identifier)

        if file_names is None:
            logger.error(f"Couldn't download APK for {package_name}.")
            return config_data

        for file_name in copy.copy(file_names):
            if os.path.splitext(file_name)[1].lower() == ".xapk":
                new_file_name: str | None = convert_to_apk(file_name=file_name,
                                                           config_data=config_data,
                                                           abi=file_names[file_name])

                if new_file_name is None:
                    return config_data

                file_names[os.path.basename(new_file_name)] = file_names[file_name]
                file_names.pop(file_name)

        try:
            config_data.Apps[package_name].version = latest_version
            config_data.Apps[package_name].filename = file_names
        except KeyError:
            config_data.Apps[package_name] = AppData(version=latest_version, filename=file_names)

        config_data = Config.model_validate(config_data)
    elif not force_patch:
        logger.info(f"No new version is supported for {package_name}, latest version has been already patched.")
        return config_data
    else:
        logger.info(f"No new version is supported for {package_name} but --force-patch was used.")

    patch_apk(config_data=config_data,
              package_name=package_name)

    write_config_file(filepath=config_path,
                      data=config_data)

    return config_data


def search_latest_version(package_name: str) -> str | None:
    logger.info(f"Searching latest available version for {package_name}...")

    with playwright_instance.new_page(user_agent=USER_AGENT) as page:
        response = page.goto(f"https://apkpure.com/aaaaa/{package_name}")
        if not response.ok:
            match response.status:
                case 401 | 403 | 404 | 410:
                    logger.warning("App not found on APKPure. Can't extract latest version. Skipping...")
                case _:
                    logger.error(f"Couldn't get latest version of {package_name} (error: {response.status}), "
                                 f"skipping...")

            return None
        page_content = page.content()

    try:
        return re.search(r"<p class=\"(?:app-)?version-name\">\s*<span>([^<]+)</span>", page_content).group(1)
    except (IndexError, AttributeError, KeyError):
        try:
            return re.search(r"<p class=\"app-version-name\">([^<]+)</p>", page_content).group(1)
        except (IndexError, AttributeError, KeyError):
            try:
                return re.search(r"<p class=\"details_sdk\">\s+<span>((?:v|v\.)?\d[^<]+)</span>", page_content).group(1)
            except (IndexError, AttributeError, KeyError):
                logger.error(f"Couldn't get latest version of {package_name}, skipping...")
                return None


def is_old_config(config_path: str) -> bool:
    try:
        if get_old_config(config_path=config_path):
            return True
        else:
            return False
    except pydantic.ValidationError:
        return False


def migrate_config(config_path: str,
                   archive_org_identifier: str | None) -> None:
    old_config: OldConfig = get_old_config(config_path=config_path)

    try:
        cli_data = ToolAppData(version=old_config.Versions["CLI"], filename=old_config.Names["CLI"])
    except (IndexError, AttributeError, KeyError):
        cli_data = ToolAppData(version="", filename="")

    try:
        patches_data = ToolAppData(version=old_config.Versions["Patches"], filename=old_config.Names["Patches"])
    except (IndexError, AttributeError, KeyError):
        patches_data = ToolAppData(version="", filename="")

    try:
        youtube_data = {
            "com.google.android.youtube": AppData(version=old_config.Versions["Original_APK"],
                                                  filename={
                                                      old_config.Names["Original_APK"]: "any"
                                                  })
        }
    except (IndexError, AttributeError, KeyError):
        youtube_data = {}

    new_config = Config(Output=old_config.Output,
                        Store_Path=old_config.Store_Path,
                        ArchiveOrg_Collection=archive_org_identifier,
                        Tools={
                            "CLI": cli_data,
                            "Patches": patches_data,
                        },
                        Name_Relations={},
                        Apps=youtube_data)

    write_config_file(filepath=config_path,
                      data=new_config)


def get_old_config(config_path: str) -> OldConfig:
    with open(config_path, "rb") as stream:
        return OldConfig.model_validate_json(stream.read(), strict=True)


def convert_to_apk(file_name: str,
                   config_data: Config,
                   abi: str) -> str | None:
    logger.info(f"Converting XAPK to APK for ABI {abi}...")

    output_filepath = os.path.join(config_data.Store_Path, os.path.splitext(file_name)[0] + ".apk")
    apkeditor_path = os.path.join(config_data.Store_Path, config_data.Tools["APKEditor"].filename)
    convert_cmd = [
        "java",
        "-jar",
        apkeditor_path,
        "m",
        "-i",
        os.path.join(config_data.Store_Path, file_name),
        "-o",
        output_filepath,
    ]

    try:
        os.remove(output_filepath)
    except FileNotFoundError:
        pass

    try:
        subprocess.run(convert_cmd, check=True, capture_output=True, encoding="utf-8")
    except subprocess.CalledProcessError as err:
        logger.opt(exception=True).error(f"Error converting {file_name} to APK.\nSTDOUT: {err.stdout}\nSTDERR: "
                                         f"{err.stderr}")
        return None

    try:
        os.remove(os.path.join(config_data.Store_Path, file_name))
    except OSError:
        logger.opt(exception=True).error(f"Error deleting {config_data.Store_Path}:")

    logger.info("Finished converting to APK.")

    return output_filepath


def get_available_files(archive_org_identifier: str) -> None:
    global ARCHIVE_ORG_AVAILABLE_FILES

    ARCHIVE_ORG_AVAILABLE_FILES = list(get_files(identifier=archive_org_identifier, glob_pattern="*apk"))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.warning("Cancel requested, exiting...")
        sys.exit(5)
