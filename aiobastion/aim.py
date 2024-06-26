import asyncio
import copy
import json
import os
import ssl
from collections import namedtuple
from http import HTTPStatus
from typing import Union, Tuple, Optional

import aiohttp
from aiohttp import ContentTypeError

from .exceptions import AiobastionException, CyberarkException, CyberarkAPIException, CyberarkAIMnotFound, AiobastionConfigurationException
from .cyberark import EPV

# AIM section
AIM_secret_resp = namedtuple('AIM_secret_resp', ['secret', 'detail'])


class EPV_AIM:
    """
    Class managing communication with the Central Credential Provider (AIM) GetPassword Web Service
    """
    _SERIALIZED_FIELDS_IN = ["host", "appid", "cert", "key", "verify", "timeout", "max_concurrent_tasks",
                             "keep_cookies", "passphrase"]
    _SERIALIZED_FIELDS_OUT = ["host", "appid", "cert", "key", "verify", "timeout", "max_concurrent_tasks",
                             "keep_cookies"]    # Exclude "passphrase"

    _GETPASSWORD_REQUEST_PARM = ["safe", "folder", "object", "username", "address", "database",
                                 "policyid", "reason", "connectiontimeout", "query", "queryformat",
                                 "failrequestonpasswordchange"]

    def __init__(self, host: Optional[str] = None, appid: Optional[str] = None, cert: Optional[str] = None, key: Optional[str] = None,
                 passphrase: Optional[str] = None, verify: Optional[Union[str, bool]] = None,
                 timeout: int = EPV.CYBERARK_DEFAULT_TIMEOUT,
                 max_concurrent_tasks: int = EPV.CYBERARK_DEFAULT_MAX_CONCURRENT_TASKS,
                 keep_cookies: bool = EPV.CYBERARK_DEFAULT_KEEP_COOKIES,
                 serialized: Optional[dict] = None):

        self.host = host
        self.appid = appid
        self.cert = cert
        self.key = key
        self.passphrase = passphrase
        self.verify = verify
        self.timeout = timeout
        self.max_concurrent_tasks = max_concurrent_tasks
        self.keep_cookies = keep_cookies                        # Whether to keep cookies between AIM calls

        # Session management
        self.__sema = None
        self.session = None
        self.request_params = None

        if serialized:
            for k, v in serialized.items():
                keyname = k.lower()
                if keyname in EPV_AIM._SERIALIZED_FIELDS_IN:
                    setattr(self, keyname, v)
                else:
                    raise AiobastionException(f"Unknown serialized AIM field: {k} = {v!r}")

        # Optional attributes
        if self.timeout is None:
            self.timeout = EPV.CYBERARK_DEFAULT_TIMEOUT

        if self.max_concurrent_tasks is None:
            self.max_concurrent_tasks = EPV.CYBERARK_DEFAULT_MAX_CONCURRENT_TASKS

        if self.verify is not False and not (isinstance(self.verify, str) and not isinstance(self.verify, bool)):
            raise AiobastionException(
                f"Invalid type for parameter 'verify' in AIM: {type(self.verify)} value: {self.verify!r}")


    @classmethod
    def _init_validate_class_attributes(cls, serialized: dict, section: str, configfile: Optional[str] = None) -> dict:
        """_init_validate_class_attributes      Initialize and validate the EPV_AIM definition (file configuration and serialized)

        Arguments:
            serialized_aim {dict}       AIM defintion
            section {str}               verified section name

        Keyword Arguments:
            configfile {str}            Name of the configuration file

        Raises:
            AiobastionConfigurationException

        Returns:
            serialized_aim {dict}       AIM defintion
        """
        serialized_aim = {
            "appid":                None,       # Default = Connection (appid)
            "cert":                 None,
            "host":                 None,       # Default = PVWA (host)
            "keep_cookies":         EPV.CYBERARK_DEFAULT_KEEP_COOKIES,
            "key":                  None,
            "max_concurrent_tasks": None,       # Default = PVWA (max_concurrent_tasks)
            "passphrase":           None,
            "timeout":              None,       # Default = PVWA (timeout)
            "verify":               None,       # Default = PVWA (PVWA_CA)
        }

        synonym_verify = 0
        synonym_max_concurrent_tasks = 0

        for k in serialized.keys():
            keyname = k.lower()

            if keyname in ["appid", "cert", "host", "key", "passphrase"]:
                serialized_aim[keyname] = serialized[k]
            elif keyname in "timeout":
                serialized_aim[keyname] = EPV_AIM._to_integer(serialized[k], keyname, section, configfile)
            elif keyname == "keep_cookies":
                if isinstance(serialized[k], bool):
                    serialized_aim[keyname] = serialized[k]
                else:
                    if configfile:
                        s = f"Invalid boolean defintion '{keyname}'  within section '{section}' in {configfile}: {serialized[k]!r}"
                    else:
                        s = f"Invalid boolean defintion '{keyname}'  within section '{section}': {serialized[k]!r}"

                    raise AiobastionConfigurationException(s)

            elif keyname in ["maxtasks", "max_concurrent_tasks"]:
                serialized_aim["max_concurrent_tasks"] = EPV_AIM._to_integer(serialized[k], keyname, section, configfile)
                synonym_max_concurrent_tasks += 1
            elif keyname in ["ca", "verify"]:
                serialized_aim["verify"] = serialized[k]
                synonym_verify += 1
            else:
                raise AiobastionConfigurationException(f"Unknown attribute '{k}' within section 'AIM' in {configfile}")

        if synonym_verify > 1:
            raise AiobastionConfigurationException(f"Duplicate synonym parameter: 'ca', 'verify' within section 'AIM'."
                                                   f"Specify only one of them.")

        if synonym_max_concurrent_tasks > 1:
            raise AiobastionConfigurationException(f"Duplicate synonym parameter: 'maxtasks', 'max_concurrent_tasks' "
                                                   f"within section 'AIM' in {configfile}."
                                                   f"Specify only one of them.")

        return serialized_aim

    @classmethod
    def _init_complete_with_pvwa(cls, serialized_aim: dict, pvwa: dict,  section: str, configfile: Optional[str] = None) -> dict:
        """_init_complete_with_pvwa     Complete initialization with pvwa definition (file configuration and serialized)

        Arguments:
            serialized_aim {dict}       AIM defintion
            pvwa {dict}                 PVWA defintion
            section {str}               verified section name

        Keyword Arguments:
            configfile {str}            Name of the configuration file

        Returns:
            serialized_aim {dict}       AIM defintion
        """
        # If not defined used Connection definitions to complete initialization.
        if serialized_aim["appid"] is None and pvwa["appid"]:
            serialized_aim["appid"]   = pvwa["appid"]

        # If not defined used PVWA definitions to complete initialization.
        if serialized_aim["host"] is None:
            serialized_aim["host"] = pvwa["host"]
        if serialized_aim["timeout"] is None:
            serialized_aim["timeout"] = pvwa["timeout"]
        if serialized_aim["max_concurrent_tasks"] is None:
            serialized_aim["max_concurrent_tasks"] =  pvwa["max_concurrent_tasks"]
        if serialized_aim["verify"] is None:
            if pvwa["verify"] is None:
                serialized_aim["verify"] = EPV.CYBERARK_DEFAULT_VERIFY
            else:
                serialized_aim["verify"] = pvwa["verify"]

        return serialized_aim

    @staticmethod
    def _to_integer(val, keyname, section, configfile):
        try:
            v = int(val)
        except ValueError:
            if configfile:
                s = f"Invalid integer defintion '{keyname}'  within section '{section}' in {configfile}: {val!r}"
            else:
                s = f"Invalid integer defintion '{keyname}'  within section '{section}': {val!r}"

            raise s

        return v



    def validate_and_setup_aim_ssl(self):
        if self.session:
            return

        # Check mandatory attributes
        for attr_name in ["host", "appid", "cert"]:
            v = getattr(self, attr_name, None)

            if v is None:
                raise AiobastionException(f"Missing AIM mandatory parameter '{attr_name}'."
                                          " Required parameters are: host, appid, key.")

        if not os.path.exists(self.cert):
            raise AiobastionException(f"Parameter 'cert' in AIM: Public certificate file not found: {self.cert!r}")

        if self.key and not os.path.exists(self.key):
            raise AiobastionException(f"Parameter 'key' in AIM: Private key certificat file not found: {self.key!r}")

        # if verify is not set, default to no ssl
        if self.verify is False:
            self.verify = EPV.CYBERARK_DEFAULT_VERIFY

        if not (isinstance(self.verify, str) or isinstance(self.verify, bool)):
            raise AiobastionException(
                f"Invalid type for parameter 'verify' (or 'CA') in AIM: {type(self.verify)} value: {self.verify!r}")

        if (isinstance(self.verify, str) and not os.path.exists(self.verify)):
            raise AiobastionException(f"Parameter 'verify' in AIM: file not found {self.verify!r}")

        if isinstance(self.verify, str):
            if not os.path.exists(self.verify):
                raise AiobastionException(f"Parameter 'verify' in AIM: file not found {self.verify!r}")

            if os.path.isdir(self.verify):
                ssl_context = ssl.create_default_context(capath=self.verify)
            else:
                ssl_context = ssl.create_default_context(cafile=self.verify)
        else:  # True or False
            ssl_context = ssl.create_default_context()

            if not self.verify:  # False
                ssl_context.check_hostname = False

        ssl_context.load_cert_chain(self.cert, keyfile=self.key, password=self.passphrase)

        self.request_params = \
            {"timeout": self.timeout,
             "ssl": ssl_context}

    @staticmethod
    def valid_secret_params(params: dict = None) -> str:
        error_str = ""

        if not isinstance(params, dict):
            error_str = "parameter is not a dictionary"
        else:
            # Must be a list of keys to modify the dictionary key (not the dictionary itself)
            for k in list(params.keys()):
                key_lower = k.lower()

                if key_lower not in EPV_AIM._GETPASSWORD_REQUEST_PARM:
                    error_str = f"unknown parameter: {k}={params[k]}"
                    break

                if k != key_lower:
                    params[key_lower] = params.pop(k)

        return error_str

    def set_semaphore(self, sema, session):
        """ Initialize the semaphore of the AIM interface,
            so that EPV and EPV_AIM could share the same semaphore.
        """
        if not self.__sema:
            self.__sema = sema

        if not self.session:
            if self.request_params is None:
                self.validate_and_setup_aim_ssl()

            self.session = session

    def to_json(self):
        serialized = {}

        for attr_name in EPV_AIM._SERIALIZED_FIELDS_OUT:
            serialized[attr_name] = getattr(self, attr_name, None)

        return serialized

    def get_url(self, url) -> Tuple[str, dict]:
        addr = f"https://{self.host}/AIMWebService/api/{url}"
        head = {"Content-type": "application/json"}

        return addr, head

    # Context manager
    async def __aenter__(self):
        self.get_aim_session()

        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close_aim_session()

    def get_aim_session(self):
        if self.session is None:
            if self.request_params is None:
                self.validate_and_setup_aim_ssl()
                # Previously we tried to use aiohttp session,
                # but now we are always doing our own session for AIM
                # TODO: test this with aiohttp.ClientSession(cookies = self.cookies)
            self.session = aiohttp.ClientSession()

        if self.__sema is None:
            self.__sema = asyncio.Semaphore(self.max_concurrent_tasks)

        return self.session

    async def close_aim_session(self):
        try:
            if self.session:
                # Are we using the epv.session, if so don't close it
                # if self.epv is None or self.epv.session is None or \
                #         (self.epv.session and self.epv.session != self.session):
                await self.session.close()
        except (CyberarkException, AttributeError):
            pass

        self.session = None
        self.__sema = None

    async def get_secret(self, **kwargs):
        """
        This function allow to search using one or more parameters and return list of address id
        :param kwargs: any searchable key = value
        |   like:  UserName, Safe, Folder, Object (which is name),
        |          Address, Database, PolicyID, Reason, Query, QueryFormat,
                   FailRequestOnPasswordChange, ...
        :raise CyberarkAIMnotFound: Account not found
        :raise CyberarkAPIException: HTTP error or CyberArk error
        :raise CyberarkException: Execution error
        :return: The password
        """

        secret_detail = await self.get_secret_detail(**kwargs)

        return secret_detail.secret

    async def get_secret_detail(self, **kwargs):
        """ Retrieve the secret from the GetPassword Web Service Central Credential Provider (AIM)

        | ℹ️ The following parameters are optional searchable keys. Refer to
            `CyberArk Central Credential Provider - REST web service`.

        :param username: User account name
        :param safe: Safe where the account is stored.
        :param object: Name of the account to retrieve (unique for a specific safe).
        :param folder: Name of the folder property
        :param address: Address account property
        :param database: Database account property
        :param policyid: Policy account property
        :param reason: The reason for retrieving the password. This reason will be audited in
            the Credential Provider audit log.
        :param query: Defines a free query using account properties, including Safe, folder, and object.
            When this method is specified, all other search criteria
            (Safe/Folder/ Object/UserName/Address/PolicyID/Database) are ignored and only the
            account properties that are specified in the query are passed to the Central
            Credential Provider in the password request.
        :param queryformat: Defines the query format, which can optionally use regular expressions.
            Possible values are: *Exact* or *Regexp*.
        :param failrequestonpasswordchange: Boolean, Whether an error will be returned if
            this web service is called when a password change process is underway or not.
        :return:  namedtuple of (secret, detail)
        |    secret = password
        |    detail = dictionary from the Central Credential Provider (AIM) GetPassword Web Service
        :raise CyberarkAIMnotFound: Account not found
        :raise CyberarkAPIException: HTTP error or CyberArk error
        :raise CyberarkException: Runtime error
        :raise AiobastionException: AIM configuration setup error
        :return:  namedtuple of (secret, detail)
            secret = password
            detail = dictionary from the Central Credential Provider (AIM) GetPassword Web Service
        """

        detail_info = await self.handle_aim_request("get", "Accounts", params=kwargs)
        secret_detail = AIM_secret_resp(detail_info["Content"], detail_info)

        return secret_detail

    @staticmethod
    def handle_error_detail_info(url: str = None, params: dict = None):
        # Mask the appid attribute, if you are a security maniac
        if "appid" in params:
            params_new = copy.copy(params)
            params_new["appid"] = "<hidden>"
        else:
            params_new = params

        return f"url: {url}, params: {params_new}"

    async def handle_aim_request(self, method: str, short_url: str, params: dict = None, filter_func=lambda x: x):
        """
        Function that handles AIM requests to the API
        :param method: "get"
        :param params: dictonary parameters for CyberArk like Safe, Object, UserName, Address,
            Reason, Query, ...
        :param short_url: piece of URL after AIMWebService/api/
        :param filter_func:
        :raise CyberarkAIMnotFound: Account not found
        :raise CyberarkAPIException: HTTP error or CyberArk error
        :raise CyberarkException: Runtime error
        :return: dictonary return by CyberArk
        """
        assert method.lower() == "get"

        url, head = self.get_url(short_url)
        session = self.get_aim_session()

        if 'applid' not in params:
            params_new = copy.copy(params)
            params_new.setdefault('appid', self.appid)
        else:
            params_new = params

        async with self.__sema:
            try:
                async with session.request(method, url, headers=head, params=params_new, **self.request_params) as req:
                    # if req.status == 404:
                    #     raise CyberarkException(f"Error 404 : Endpoint {url} not found")

                    try:
                        resp_json = await req.json()
                        if req.status == 200:
                            if "Content" not in resp_json:
                                raise CyberarkAPIException(req.status, "INVALID_JSON",
                                                           "Could not find the password ('Content')",
                                                           EPV_AIM.handle_error_detail_info(url, params_new))

                            return filter_func(resp_json)
                        else:
                            # This is a error
                            if "Details" in resp_json:
                                details = resp_json["Details"]
                            else:
                                details = EPV_AIM.handle_error_detail_info(url, params_new)

                            if "ErrorCode" in resp_json and "ErrorMsg" in resp_json:
                                if resp_json["ErrorCode"] == "APPAP004E":
                                    raise CyberarkAIMnotFound(req.status, resp_json["ErrorCode"], resp_json["ErrorMsg"],
                                                              details)
                                else:
                                    raise CyberarkAPIException(req.status, resp_json["ErrorCode"],
                                                               resp_json["ErrorMsg"], details)
                            else:
                                http_error = HTTPStatus(req.status)

                                raise CyberarkAPIException(req.status, "HTTP_ERR_CODE", http_error.phrase, details)

                    except json.decoder.JSONDecodeError as err:
                        http_error = HTTPStatus(req.status)
                        details = EPV_AIM.handle_error_detail_info(url, params_new)
                        raise CyberarkAPIException(req.status, "HTTP_ERR_CODE", http_error.phrase, details) from err

                    except (KeyError, ValueError, ContentTypeError) as err:
                        # http_error = HTTPStatus(req.status)
                        print(await req.text())
                        details = EPV_AIM.handle_error_detail_info(url, params_new)
                        raise CyberarkException(
                            f"HTTP error {req.status}: {str(err)} || Additional Details : {details}") from err

            except aiohttp.ClientError as err:
                details = EPV_AIM.handle_error_detail_info(url, params_new)
                raise CyberarkException(f"HTTP error: {str(err)} || Additional Details : {details}") from err
