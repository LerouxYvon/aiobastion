# -*- coding: utf-8 -*-

import yaml
import warnings
from .exceptions import AiobastionConfigurationException
from .cyberark import EPV
from .accounts import Account
from .safe import Safe
from .aim import EPV_AIM
# from .aim import EPV_AIM


class Config:
    """Parse a config file into an object"""

    def __init__(self, configfile):
        self.configfile = configfile

        # Global section initialization
        self.AIM = None
        self.Connection = None
        self.CPM = ""
        self.custom = None
        self.customIPField = None
        self.Label = None
        self.retention = None

        # Connection section initialization
        self.appid = None
        self.authtype = "Cyberark"
        self.password = None
        self.user_search = None
        self.username = None

        # PVWA section initialization
        self.PVWA = None
        self.max_concurrent_tasks = EPV.CYBERARK_DEFAULT_MAX_CONCURRENT_TASKS
        self.timeout = EPV.CYBERARK_DEFAULT_TIMEOUT
        self.PVWA_CA = EPV.CYBERARK_DEFAULT_VERIFY
        self.keep_cookies = EPV.CYBERARK_DEFAULT_KEEP_COOKIES

        # Optional configuration like account, safe, ...
        options_modules = {}

        for module in EPV.CYBERARK_OPTIONS_MODULES_LIST:
            self.options_modules[module] = {}

        with open(configfile, 'r') as config:
            configuration = yaml.safe_load(config)

        # Change global section name in lowercase
        for k in list(configuration.keys()):
            keyname = k.lower()

            if keyname not in [
                               "aim", "connection", "cpm",
                               "label", "pvwa", "retention",
                               "custom",                # Customer use only (not aibastion)
                               "customipfield",         # Compatibility - deprecated
                               ]:

                if keyname not in EPV.CYBERARK_OPTIONS_MODULES_LIST:
                    warnings.warn(f"aiobastion - Unknown section '{k}' in {self.configfile}")
                continue

            if k != keyname:
                # change the keyname in lowercase
                configuration[keyname] = configuration.pop(k)

        # Read global sections in the right order
        if "connection" in configuration and configuration["connection"]:
            self._read_section_connection(configuration["connection"])

        if "pvwa" in configuration and configuration["pvwa"]:
            self._read_section_pvwa(configuration["pvwa"])

        if "label" in configuration:
            self.label = configuration["label"]
        if "custom" in configuration:
            self.custom = configuration["custom"]
        if "customipfield" in configuration:
            self.customIPField = configuration["customipfield"]

        # --------------------------------------------
        # opions_modules
        # --------------------------------------------
        # AIM module
        if "aim" in configuration and configuration["aim"]:
            self.options_modules["AIM"] = self._read_section_aim(configuration["aim"])


        # account module
        if self.custom and \
            ("LOGON_ACCOUNT_INDEX" in self.custom or
             "RECONCILE_ACCOUNT_INDEX" in self.custom):
            raise AiobastionConfigurationException("Please move from the custom to account section the 'logon_account_index' and 'reconcile_account_index' definition in {self.configfile}.")

        if "account" in configuration and configuration["account"]:
            self.options_modules["account"] = Account._init_validate_class_attributes(configuration["account"], "account", self.configfile)
        else:
            self.options_modules["account"] = Account._init_validate_class_attributes({}, "account", self.configfile)

        # Safe module
        if "safe" in configuration and \
            ("cmp" in configuration or "retention" in configuration):
            raise AiobastionConfigurationException("Please move from the global to account section the 'cmp' and 'retention' definition in {self.configfile}.")

        if "safe" in configuration and configuration["safe"]:
            self.options_modules["safe"] = Safe._init_validate_class_attributes(configuration["safe"], "safe", self.configfile)
        else:
            self.options_modules["safe"] = Safe._init_validate_class_attributes({}, "safe", self.configfile)

        if "cpm" in configuration:
            self.options_modules["safe"]["cpm"] = configuration["cpm"]  # module safe.py

        if "retention" in configuration:
            self.options_modules["safe"]["retention"] = self._to_integer("retention", configuration["retention"])  # module safe.py



    def _read_section_connection(self, configuration):
        for k in list(configuration.keys()):
            keyname = k.lower()

            if keyname == "appid":
                self.appid = configuration[k]
            elif keyname == "authtype":
                self.authtype = configuration[k]
            elif keyname == "password":
                self.password = configuration[k]
            elif keyname == "user_search":
                self.user_search = configuration[k]
            elif keyname == "username":
                self.username = configuration[k]
            else:
                raise AiobastionConfigurationException(f"Unknown attribute '{k}' within section 'connection' "
                                                       f"in {self.configfile}")

        # user_search dictionary Validation
        if self.user_search:
            if not isinstance(self.user_search, dict):
                raise AiobastionConfigurationException(f"Malformed attribute 'user_search' within section "
                                                       f"'connection' in {self.configfile}: {self.user_search!r}")


            for k in list(self.user_search.keys()):
                keyname = k.lower()

                # Check user_search parameter name
                if keyname not in EPV_AIM._GETPASSWORD_REQUEST_PARM:
                    raise AiobastionConfigurationException(f"Unknown attribute '{k}' within section "
                                                           f"'connection/user_search' in {self.configfile}")

                if k != keyname:
                    self.user_search[keyname] = self.user_search.pop(k)

    def _read_section_pvwa(self, configuration):
        synonym_PVWA_CA = 0
        synonym_max_concurrent_tasks = 0

        for k in list(configuration.keys()):
            keyname = k.lower()

            if keyname == "host":
                self.PVWA = configuration[k]
            elif keyname == "timeout":
                self.timeout = self._to_integer("PVWA/" + k, configuration[k])
            elif keyname == "maxtasks" or keyname == "max_concurrent_tasks":
                self.max_concurrent_tasks = self._to_integer("PVWA/" + k, configuration[k])
                synonym_max_concurrent_tasks += 1
            elif keyname == "keep_cookies":
                self.keep_cookies = self._to_bool("PVWA/" + k, configuration[k])
            elif keyname == "verify" or keyname == "ca":
                self.PVWA_CA = configuration[k]
                synonym_PVWA_CA += 1
            else:
                raise AiobastionConfigurationException(f"Unknown attribute '{k}' within section 'PVWA' in {self.configfile}")

        if synonym_PVWA_CA > 1:
            raise AiobastionConfigurationException(f"Duplicate synonym parameter: 'ca', 'verify' within section 'PVWA' "
                                                   f"in {self.configfile}. Specify only one of them.")

        if synonym_max_concurrent_tasks > 1:
            raise AiobastionConfigurationException(f"Duplicate synonym parameter: 'maxtasks', 'max_concurrent_tasks' "
                                                   f"within section 'PVWA' in {self.configfile}. "
                                                   f"Specify only one of them.")

    def _read_section_aim(self, configuration):
        self.AIM = EPV_AIM._init_validate_class_attributes(configuration, "AIM", self.configfile)

        # Value that may come from PVWA
        pvwa = {
            "appid": self.AIM["appid"],
            "host": self.PVWA,
            "timeout": self.timeout,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "verify": self.PVWA_CA
        }

        return EPV_AIM._init_complete_with_pvwa(self.options_modules["aim"], pvwa, "AIM", self.configfile)


    def _read_section_account(self, module, configuration):
        for k in list(configuration.keys()):
            keyname = k.lower()

            if keyname == "logon_account_index":
                self.options_modules[module][k] = self._to_integer(module + "/" + k, configuration[k])
            elif keyname == "reconcile_account_index":
                self.options_modules[module][k] = self._to_integer(module + "/" + k, configuration[k])
            else:
                raise AiobastionConfigurationException(f"Unknown attribute '{k}' within section '{module}' "
                                                       f"in {self.configfile}")


    def _read_section_safe(self, module, configuration):
        for k in list(configuration.keys()):
            keyname = k.lower()

            if keyname == "cpm":
                self.options_modules[module][k] = configuration[k]
            elif keyname == "retention":
                self.options_modules[module][k] = self._to_integer(module + "/" + k, configuration[k])
            else:
                raise AiobastionConfigurationException(f"Unknown attribute '{k}' within section '{module}' "
                                                       f"in {self.configfile}")

    def _to_integer(self, section_key, val):
        try:
            v = int(val)
        except ValueError:
            raise AiobastionConfigurationException(f"Invalid integer within '{section_key}'"
                                                   f" in {self.configfile}: {val!r}")

        return v

    def _to_bool(self, section_key, val):
        if isinstance(val, bool):
            v = val
        elif isinstance(val, str):
            # In case the value has been speficied has a string (see https://yaml.org/type/bool.html)
            val = val.lower()

            if val in ["y", "yes", "true", "on"]:
                v = True
            elif val in ["n", "no", "false", "off"]:
                v = False
            else:
                raise AiobastionConfigurationException(f"Invalid boolean within '{section_key}'"
                                    f" in {self.configfile}: {val!r}")
        else:
            raise AiobastionConfigurationException(f"Invalid boolean within '{section_key}'"
                                                   f" in {self.configfile}: {val!r}")

        return v

# No rights at all
DEFAULT_PERMISSIONS = {
        "UseAccounts": False,
        "RetrieveAccounts": False,
        "ListAccounts": False,
        "AddAccounts": False,
        "UpdateAccountContent": False,
        "UpdateAccountProperties": False,
        "InitiateCPMAccountManagementOperations": False,
        "SpecifyNextAccountContent": False,
        "RenameAccounts": False,
        "DeleteAccounts": False,
        "UnlockAccounts": False,
        "ManageSafe": False,
        "ManageSafeMembers": False,
        "BackupSafe": False,
        "ViewAuditLog": False,
        "ViewSafeMembers": False,
        "AccessWithoutConfirmation": False,
        "CreateFolders": False,
        "DeleteFolders": False,
        "MoveAccountsAndFolders": False
    }

# Can create object
PROV_PERMISSIONS = dict(DEFAULT_PERMISSIONS)
PROV_PERMISSIONS.update({
        "ListAccounts": True,
        "AddAccounts": True,
        "UpdateAccountContent": True,
        "UpdateAccountProperties": True,
        "InitiateCPMAccountManagementOperations": True,
        "RenameAccounts": True,
        "DeleteAccounts": True,
        "ManageSafe": False,
        "ManageSafeMembers": False,
        "ViewSafeMembers": False,
        "AccessWithoutConfirmation": True,
        "CreateFolders": True,
        "DeleteFolders": True,
        "MoveAccountsAndFolders": True
    })

MANAGER_PERMISSIONS = dict(PROV_PERMISSIONS)
MANAGER_PERMISSIONS.update({
    "ManageSafe": True,
    "ManageSafeMembers": True,
    "ViewSafeMembers": True,
})

# all to true
ADMIN_PERMISSIONS = {perm: True for perm in DEFAULT_PERMISSIONS}

# connect
USE_PERMISSIONS = dict(DEFAULT_PERMISSIONS)
USE_PERMISSIONS["UseAccounts"] = True
USE_PERMISSIONS["ListAccounts"] = True
# Connect does not necessarily require AccessWithoutConfirmation
# USE_PERMISSIONS["AccessWithoutConfirmation"] = True

# use + retrieve
SHOW_PERMISSIONS = dict(USE_PERMISSIONS)
SHOW_PERMISSIONS["RetrieveAccounts"] = True

# list accounts + audit part
AUDIT_PERMISSIONS = dict(DEFAULT_PERMISSIONS)
AUDIT_PERMISSIONS["ListAccounts"] = True
AUDIT_PERMISSIONS["ViewAuditLog"] = True
AUDIT_PERMISSIONS["ViewSafeMembers"] = True

# power user = SHOW + AUDIT
POWER_PERMISSIONS = dict(DEFAULT_PERMISSIONS)
POWER_PERMISSIONS.update({k: v for k, v in SHOW_PERMISSIONS.items() if v})
POWER_PERMISSIONS.update({k: v for k, v in AUDIT_PERMISSIONS.items() if v})

CPM_PERMISSIONS = {
        "UseAccounts": True,
        "RetrieveAccounts": True,
        "ListAccounts": True,
        "AddAccounts": True,
        "UpdateAccountContent": True,
        "UpdateAccountProperties": True,
        "InitiateCPMAccountManagementOperations": True,
        "SpecifyNextAccountContent": True,
        "RenameAccounts": True,
        "DeleteAccounts": True,
        "UnlockAccounts": True,
        "ManageSafe": False,
        "ManageSafeMembers": False,
        "BackupSafe": False,
        "ViewAuditLog": True,
        "ViewSafeMembers": False,
        "RequestsAuthorizationLevel1": False,
        "RequestsAuthorizationLevel2": False,
        "AccessWithoutConfirmation": False,
        "CreateFolders": True,
        "DeleteFolders": True,
        "MoveAccountsAndFolders": True
}

# v2 perm
V2_BASE = {
    "useAccounts": True,
    "retrieveAccounts": False,
    "listAccounts": True,
    "addAccounts": False,
    "updateAccountContent": False,
    "updateAccountProperties": False,
    "initiateCPMAccountManagementOperations": False,
    "specifyNextAccountContent": False,
    "renameAccounts": False,
    "deleteAccounts": False,
    "unlockAccounts": False,
    "manageSafe": False,
    "manageSafeMembers": False,
    "backupSafe": False,
    "viewAuditLog": False,
    "viewSafeMembers": False,
    "accessWithoutConfirmation": False,
    "createFolders": False,
    "deleteFolders": False,
    "moveAccountsAndFolders": False,
    "requestsAuthorizationLevel1": False,
    "requestsAuthorizationLevel2": False
}

V2_USE = {
    "useAccounts": True,
    "listAccounts": True,
}
V2_ADMIN = {"manageSafeMembers": True}
V2_CHANGE = {"updateAccountContent": True}
V2_SHOW = {"retrieveAccounts": True}
V2_AUDIT = {"viewAuditLog": True}

#
# # power user = SHOW + AUDIT
# V2_POWER = dict(V2_SHOW)
# V2_POWER.update({k: v for k, v in V2_AUDIT.items() if v})


def validate_ip(s):
    a = s.split('.')
    if len(a) != 4:
        return False
    for x in a:
        if not x.isdigit():
            return False
        i = int(x)
        if i < 0 or i > 255:
            return False
    return True

def flatten(A):
    rt = []
    for i in A:
        if isinstance(i,list):
            rt.extend(flatten(i))
        else:
            rt.append(i)
    return rt

def permissions(profile: str) -> dict:
    if "admin" in profile.lower():
        return ADMIN_PERMISSIONS
    if "use" in profile.lower():
        return USE_PERMISSIONS
    if "show" in profile.lower():
        return SHOW_PERMISSIONS
    if "audit" in profile.lower():
        return SHOW_PERMISSIONS
    if "prov" in profile.lower():
        return PROV_PERMISSIONS
    if "power" in profile.lower():
        return POWER_PERMISSIONS
    if "cpm" in profile.lower():
        return CPM_PERMISSIONS
    if "manager" in profile.lower():
        return MANAGER_PERMISSIONS
    else:
        # nothing !
        return DEFAULT_PERMISSIONS


def get_v2_profile(permission) -> str:
    perms = []
    if all(t for t in [permission[k] == v for k, v in V2_ADMIN.items()]):
        perms.append("Admin")
    if all(t for t in [permission[k] == v for k, v in V2_AUDIT.items()]):
        perms.append("Audit")
    if all(t for t in [permission[k] == v for k, v in V2_SHOW.items()]):
        perms.append("Show")
    if all(t for t in [permission[k] == v for k, v in V2_CHANGE.items()]):
        perms.append("Change")
    if all(t for t in [permission[k] == v for k, v in V2_USE.items()]):
        perms.append("Use")
    if len(perms) == 0:
        perms.append("Profil Inconnu")
    return " + ".join(perms)
