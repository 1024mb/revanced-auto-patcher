import argparse
import copy
import json
import os
import re
import subprocess
import sys
from typing import Optional, Dict

import requests

from __init__ import __version__

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0"
PLATFORM = sys.platform


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

    args = parser.parse_args()

    init = args.init  # type: bool
    conf = args.conf[0]  # type: str
    output = args.output[0]  # type: str
    store_path = args.store_path[0]  # type: str

    if init:
        init_(conf, output, store_path)

    start_process(conf)


def init_(conf: str,
          output: str,
          store_path: str) -> None:

    config = {
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
        print("Config file doesn't exist, you have to initialize.")
        sys.exit(1)
    elif not os.path.isfile(conf):
        print("Supplied config file path is not a file.")
        sys.exit(1)

    config = get_config(conf)
    latest_versions = get_latest_versions()

    current_versions = {
        "CLI": config["Versions"]["CLI"],
        "Patches": config["Versions"]["Patches"],
        "Patches_JSON": config["Versions"]["Patches_JSON"],
        "Integrations": config["Versions"]["Integrations"]
    }

    new_ver_available = False
    for tool in current_versions.keys():
        if latest_versions[tool]["URL"] is None or latest_versions[tool]["Name"] is None:
            continue
        if latest_versions[tool]["Version"] != current_versions[tool]:
            new_ver_available = True
            download_latest_version(url=latest_versions[tool]["URL"],
                                    name=latest_versions[tool]["Name"],
                                    download_path=config["Store_Path"])
        else:
            print(f"{tool} is already updated to the latest version.")

    if new_ver_available:
        write_new_versions_and_names(latest_versions, conf, config)

    current_yt_version = config["Versions"]["Original_APK"]
    latest_yt_version = get_latest_supported_yt_version(conf)

    if compare_versions(current_yt_version, latest_yt_version):
        print("New YT version supported, downloading and patching...")
        download_latest_yt_apk(conf, latest_yt_version)
    else:
        print("No new YT version is supported, latest version has been already patched.")
        sys.exit(0)

    latest_yt_version = {
        "Original_APK": {
            "Name": f"com.google.android.youtube.{latest_yt_version}.apk",
            "Version": latest_yt_version
        }
    }

    write_new_versions_and_names(latest_yt_version, conf, get_config(conf))

    patch_latest_yt_apk(conf)

    print("All done.")


def get_config(conf: str) -> dict:
    with open(conf, "r", encoding="utf-8", errors="backslashreplace") as stream:
        return copy.deepcopy(json.loads(stream.read()))


def get_latest_versions() -> Dict[str, Dict[str, Optional[str]]]:
    latest_cli = get_latest_cli()
    latest_patch_bundle = get_latest_patch_bundle()
    latest_patch_json = get_latest_patch_json()
    latest_integrations = get_latest_integrations_apk()

    return {
        "CLI": latest_cli,
        "Patches": latest_patch_bundle,
        "Patches_JSON": latest_patch_json,
        "Integrations": latest_integrations
    }


def get_latest_cli() -> Dict[str, Optional[str]]:
    url = "https://api.github.com/repos/revanced/revanced-cli/releases"

    newest_version, newest_version_url, newest_version_name = get_latest_version_name_and_url(url, "jar", "cli")

    return {
        "Name": newest_version_name,
        "Version": newest_version,
        "URL": newest_version_url
    }


def get_latest_patch_bundle() -> Dict[str, Optional[str]]:
    url = "https://api.github.com/repos/revanced/revanced-patches/releases"

    newest_version, newest_version_url, newest_version_name = get_latest_version_name_and_url(url,
                                                                                              "jar",
                                                                                              "patch bundle")

    return {
        "Name": newest_version_name,
        "Version": newest_version,
        "URL": newest_version_url
    }


def get_latest_patch_json() -> Dict[str, Optional[str]]:
    url = "https://api.github.com/repos/revanced/revanced-patches/releases"

    newest_version, newest_version_url, newest_version_name = get_latest_version_name_and_url(url, "json", "patch json")

    return {
        "Name": newest_version_name,
        "Version": newest_version,
        "URL": newest_version_url
    }


def get_latest_integrations_apk() -> Dict[str, Optional[str]]:
    url = "https://api.github.com/repos/revanced/revanced-integrations/releases"

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

    sess = requests.session()
    sess.headers.update({"User-Agent": USER_AGENT})

    resp = sess.get(url, allow_redirects=True)

    if not 199 < resp.status_code < 300:
        print(f"There was an error fetching the latest version for \"{tool_name}\": {resp.status_code}")
        sys.exit(1)

    json_data = json.loads(resp.content)

    found_stable = False
    i = 0
    i_max = len(json_data) - 1

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

    return re.sub(r"^v\.?", "", newest_version), newest_version_url, newest_version_name


def get_download_url(data: dict,
                     extension: str,
                     tool_name: str) -> tuple[Optional[str], Optional[str]]:
    item_number = len(data["assets"])

    if item_number == 0:
        print(f"No assets found for \"{tool_name}\"")
        return None, None

    for i in range(0, item_number):
        if data["assets"][i]["name"].lower().endswith(extension):
            return data["assets"][i]["browser_download_url"], data["assets"][i]["name"]

    print(f"No matching assets found for \"{tool_name}\" [expected extension: {extension}]")

    return None, None


def download_latest_version(url: str,
                            name: str,
                            download_path: str) -> None:
    name = sanitize_name(name)
    os.makedirs(download_path, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    response = session.get(url, stream=True)

    with open(os.path.join(download_path, name), "wb", buffering=0) as stream:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                stream.write(chunk)


def sanitize_name(name: str) -> str:
    illegal_characters = {}

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
        illegal_characters = {"/": "\uFF0F"}

    for character in illegal_characters.keys():
        if character in name:
            name = name.replace(character, illegal_characters[character].encode("utf-8").decode("utf-8"))

    return name


def get_latest_supported_yt_version(conf: str) -> str:
    config = get_config(conf)

    json_path = os.path.join(config["Store_Path"], config["Names"]["Patches_JSON"])
    patches_json = json.load(open(json_path, "r", encoding="utf-8", errors="backslashreplace"))

    latest_versions = []

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
        print("No supported version found for Youtube. Unless something has changed since this program has been "
              "written, this shouldn't happen.")
        sys.exit(2)

    if len(latest_versions) == 1:
        return latest_versions[0]
    else:
        newest_found = ""
        for version_to_check in latest_versions:
            if compare_versions(version_to_check, newest_found):
                newest_found = version_to_check
        return newest_found


def compare_versions(current: str,
                     latest: str) -> bool:
    if current == "" or latest == "":
        return True

    current_version = re.sub(r"^v\.?", "", current)
    latest_version = re.sub(r"^v\.?", "", latest)

    current_version = current_version.split(".")
    latest_version = latest_version.split(".")

    if len(current_version) > len(latest_version):
        return True
    else:
        # if they have equal length, this will still return the correct number
        min_number = min(len(current_version), len(latest_version))

        return compare_version_numbers(current_version, latest_version, min_number)


def compare_version_numbers(current_version: list,
                            latest_version: list,
                            number: int) -> bool:
    """
    Should return True if "latest_version" is higher than "current_version".
    :param current_version: supposed old version
    :param latest_version: supposed new version
    :param number: amount of items in the list that will be iterated
    :return: Whether the "latest_version" is higher than the "current_version"
    """
    for i in range(number):
        current_ver = current_version[i]
        latest_ver = latest_version[i]

        pad = max(len(current_ver), len(latest_ver)) - 1

        current_ver = end_fill(current_ver, pad)
        latest_ver = end_fill(latest_ver, pad)

        if int(current_ver) > int(latest_ver):
            return False
        elif int(current_ver) < int(latest_ver):
            return True
        else:
            continue

    return False


def write_new_versions_and_names(latest_versions: dict,
                                 conf: str,
                                 config: dict) -> None:
    new_config = copy.deepcopy(config)

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


def download_latest_yt_apk(conf: str,
                           version: str) -> None:
    url = f"https://apkpure.com/youtube/com.google.android.youtube/download/{version}"
    config = get_config(conf)

    session = requests.session()
    session.headers.update({"User-Agent": USER_AGENT})

    resp = session.get(url)

    if resp.status_code != 200:
        sys.exit(1)

    html_content = resp.content.decode("utf-8")

    pattern = r"<span class=\"[^\"]+\" data-tag=\"APK\">APK</span>.+?class=\"download-btn\"\shref=\"([^\"]+)\""
    new_url = re.search(pattern, html_content, re.IGNORECASE).groups()[0]

    new_resp = session.get(new_url, allow_redirects=True, stream=True)

    with open(os.path.join(config["Store_Path"], f"com.google.android.youtube.{version}.apk"), "wb") as stream:
        for chunk in new_resp.iter_content(chunk_size=8192):
            if chunk:
                stream.write(chunk)


def patch_latest_yt_apk(conf: str) -> None:

    config = get_config(conf)

    cli_path = os.path.join(config["Store_Path"], config["Names"]["CLI"])
    patches_path = os.path.join(config["Store_Path"], config["Names"]["Patches"])
    integrations_path = os.path.join(config["Store_Path"], config["Names"]["Integrations"])
    output_file = os.path.join(config["Output"],
                               f"app.revanced.android.youtube.{config['Versions']['Original_APK']}.apk")
    input_file = os.path.join(config["Store_Path"], config["Names"]["Original_APK"])

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
        subprocess.check_output(command, encoding="utf-8")
    except subprocess.CalledProcessError as e:
        print("Error patching APK", end="\n\n")
        print(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
