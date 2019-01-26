import zipfile
import os
import signal
import psutil
import pyani.core.util
import logging
import pyani.core.ui
import pyani.core.anivars
from pyani.core.toolsinstall import AniToolsSetup

# set the environment variable to use a specific wrapper
# it can be set to pyqt, pyqt5, pyside or pyside2 (not implemented yet)
# you do not need to use QtPy to set this variable
os.environ['QT_API'] = 'pyqt'
# import from QtPy instead of doing it directly
# note that QtPy always uses PyQt5 API
from qtpy import QtWidgets, QtCore


logger = logging.getLogger()


class AniAppMngr(object):
    """
    Class to manage an app. Does installs and updates
    """
    def __init__(self, app_name):
        self.__log = []

        # used to update app data and apps, and get any new apps.
        self.tools_setup = AniToolsSetup()
        # just using show vars, no sequence or shot vars
        self.ani_vars = pyani.core.anivars.AniVars()

        # these are the same for all apps
        self.__app_data_path = "C:\\PyAniTools\\app_data\\"
        self.__updater_app = "C:\\PyAniTools\\installed\\PyAppMngr\\PyAppMngr.exe"
        # per app variables
        self.__app_name = app_name
        self.__app_doc_page = "http://172.18.10.11:8090/display/KB/{0}".format(self.app_name)
        self.__tools_install_dir = "C:\\PyAniTools\\installed"
        self.__app_install_path = "{0}\\{1}".format(self.tools_install_dir, app_name)
        self.__app_exe = "{0}.exe".format(self.app_name)
        self.__app_package = "C:\\PyAniTools\\packages\\{0}.zip".format(self.app_name)
        self.__user_config = os.path.abspath("{0}\\app_pref.json".format(self.app_install_path))
        self.__app_config = os.path.abspath("{0}{1}\\app_data.json".format(self.app_data_path, self.app_name))
        # load data from json files and log error if one occurs
        self.__user_data = pyani.core.util.load_json(self.user_config)
        if not isinstance(self.__user_data, dict):
            self.__log.append(self.__user_data)
            logger.error(self.__user_data)
            self.__user_data = None
        self.__app_data = pyani.core.util.load_json(self.app_config)
        if not isinstance(self.__app_data, dict):
            self.__log.append(self.__app_data)
            logger.error(self.__app_data)
            self.__app_data = None

        # try to set user version
        if self.__user_data:
            self.__user_version = self.__user_data["version"]
        else:
            self.__user_version = None
        # try to get latest version
        if self.__app_data:
            self.__latest_version = self.__app_data["versions"][0]["version"]
        else:
            self.__latest_version = None
        # try to get release notes
        if self.__app_data:
            self.__features = ", ".join(self.__app_data["versions"][0]["features"])
        else:
            self.__features = None

    @property
    def log(self):
        """Log of errors as list
        """
        return self.__log

    @property
    def app_doc_page(self):
        """Address Url of application documentation
        """
        return self.__app_doc_page

    @property
    def user_version(self):
        """The version the user has installed.
        """
        return self.__user_version

    @property
    def tools_install_dir(self):
        """Location where tools are installed
        """
        return self.__tools_install_dir

    @property
    def latest_version(self):
        """The version on the server
        """
        return self.__latest_version

    @property
    def updater_app(self):
        """The file path to the python updater script
        """
        return self.__updater_app

    @property
    def app_exe(self):
        """The app executable name
        """
        return self.__app_exe

    @property
    def app_package(self):
        """The app zip file
        """
        return self.__app_package

    @property
    def app_data_path(self):
        """The path to where application data lives - non user specific
        """
        return self.__app_data_path

    @property
    def app_name(self):
        """The name of the app
        """
        return self.__app_name

    @property
    def app_install_path(self):
        """The file path to the app.exe on the users computer
        """
        return self.__app_install_path

    @property
    def user_config(self):
        """The user's preference file
        """
        return self.__user_config

    @property
    def app_config(self):
        """The app config file
        """
        return self.__app_config

    @property
    def features(self):
        """The apps features or release notes list
        """
        return self.__features

    def install(self):
        """Installs the latest version of the app
        :return Error if encountered, None if no errors
        """
        # remove the existing app
        if os.path.exists(self.app_package) and zipfile.is_zipfile(self.app_package):
            error = pyani.core.util.rm_dir(self.app_install_path)
            if error:
                return error
            # unzip new app files
            error = self.unpack_app(self.app_package, self.app_install_path)
            if error:
                return error

            self._update_user_version()

            return None
        else:
            error = "The zip file {0} is invalid or does not exist, cannot install_apps.".format(self.app_package)
            logging.error(error)
            return error

    @staticmethod
    def unpack_app(package, install_path):
        """
        Unzip a zip file with an application  inside
        :param package: the zip file containing the package
        :param install_path: the place to unzip
        :return error if encountered, otherwise None

        """
        try:
            with zipfile.ZipFile(file=package) as zipped:
                zipped.extractall(path=install_path)
            return None
        except (zipfile.BadZipfile, zipfile.LargeZipFile, IOError, OSError) as e:
            error = "{0} update file is corrupt. Error is {1}".format(package, e)
            logger.exception(error)
            return error

    @staticmethod
    def kill_process(pid):
        """
        Stop a running process
        :param pid: the process id
        :return: exception if there is one, None otherwise
        """
        try:
            os.kill(pid, signal.SIGINT)
        except Exception as exc:
            return exc
        return None

    @staticmethod
    def find_processes_by_name(name):
        """
        Find a list of processes matching 'name'.
        :param name: the name of the process to find
        :return: the list of process ids
        """
        assert name, name
        process_list = []
        for process in psutil.process_iter():
            name_, exe, cmdline = "", "", []
            try:
                name_ = process.name()
                exe = process.exe()
            except (psutil.AccessDenied, psutil.ZombieProcess):
                pass
            except psutil.NoSuchProcess:
                continue
            if name == name_ or os.path.basename(exe) == name:
                process_list.append(process.pid)
        return process_list

    def is_latest(self):
        """Checks if user has the latest version
        :return False if there is a new version, True if on the latest version. Returns None if the app data isn't
        loaded
        """
        if isinstance(self.__app_data, dict):
            latest_version = self.__app_data["versions"][0]["version"]
            if not self.__user_data["version"] == latest_version:
                return False
            else:
                return True
        else:
            return None

    def _update_user_version(self):
        """Updates the user version - call after updating an app
        """
        self.__user_data = pyani.core.util.load_json(self.user_config)
        self.__user_version = self.__user_data["version"]

    def download_update(self):
        """
        Downloads the files from cgt.
        :return True if downloaded, False if no updates to download, error if encountered.
        """
        # update
        return self.tools_setup.download_updates()

    def install_update(self):
        """
        Installs downloaded files from cgt.
        :return If successful returns None, otherwise returns error
        """
        # MAKE MAIN DIRECTORY ON C DRIVE --------------------------------------------
        error, created = self.tools_setup.make_install_dirs()
        if error:
            return error

        # APP DATA -------------------------------------------------------------------
        error = self.tools_setup.update_app_data()
        if error:
            return error

        # SETUP PACKAGES ------------------------------------------------------------
        error = self.tools_setup.update_packages()
        if error:
            return error

        # SETUP APPS ---------------------------------------------------------------
        # first install_apps
        if not os.path.exists(self.tools_setup.app_vars.apps_dir):
            error, created_shortcuts = self.tools_setup.first_time_setup()
            if error:
                return error
        # already installed
        else:
            error, new_apps = self.tools_setup.add_new_apps()
            if error:
                return error

        # NUKE --------------------------------------------------------------------
        # first check for .nuke  folder in C:Users\username
        error, created = self.tools_setup.make_nuke_dir()
        if error:
            return error

        # check for  custom nuke folder in .nuke
        error, created = self.tools_setup.make_custom_nuke_dir()
        if error:
            return error

        # copy custom init.py, menu.py, and .py (script with python code to support menu and gizmos)
        # Note: remove the files first, copy utils seem to not like existing files
        error = self.tools_setup.copy_custom_nuke_init_and_menu_files()
        if error:
            return error
        # finally update the init.py - only append, don't want to lose existing code added by user
        error, added_plugin_path = self.tools_setup.add_custom_nuke_path_to_init()
        if error:
            return error

        # update sequence list
        error = self.tools_setup.update_show_info()
        if error:
            return "Sequence List Update Failed. Error is {0}".format(error)

        # update install_apps date
        error = self.tools_setup.set_install_date()
        if error:
            return error


class AniAppMngrGui(pyani.core.ui.AniQMainWindow):
    """
    Gui class for app manager. Shows installed apps, versions, and updates if available.
    :param error_logging : error log (pyani.core.error_logging.ErrorLogging object) from trying
    to create logging in main program
    """
    def __init__(self, error_logging):
        self.log = []

        # build main window structure
        self.app_name = "PyAppMngr"
        self.app_mngr = pyani.core.appmanager.AniAppMngr(self.app_name)
        # pass win title, icon path, app manager, width and height
        super(AniAppMngrGui, self).__init__(
            "Py App Manager",
            "Resources\\pyappmngr_icon.ico",
            self.app_mngr,
            800,
            400,
            error_logging
        )

        # check if logging was setup correctly in main()
        if error_logging.error_log_list:
            errors = ', '.join(error_logging.error_log_list)
            self.msg_win.show_warning_msg(
                "Error Log Warning",
                "Error logging could not be setup because {0}. You can continue, however "
                "errors will not be logged.".format(errors)
            )

        # save the setup class for error logging to use later
        self.error_logging = error_logging

        self.task_scheduler = pyani.core.util.WinTaskScheduler(
            "pyanitools_update",
            os.path.join(self.app_mngr.tools_install_dir, "PyAniToolsUpdate.exe")
        )

        # list of apps
        app_list_json_path = "C:\\PyAniTools\\app_data\\Shared\\app_list.json"
        self.app_names = pyani.core.util.load_json(app_list_json_path)
        # list of app managers for each app
        self.app_mngrs = []
        if not isinstance(self.app_names, list):
            error = "Critical error loading list of applications from {0}".format(app_list_json_path)
            logger.error(error)
            self.msg_win.show_error_msg("Critical Error", error)
        else:
            for name in self.app_names:
                app_mngr = AniAppMngr(name)
                if app_mngr.log:
                    self.msg_win.show_warning_msg(
                        "Warning",
                        "Could not correctly load data for {0}. This application will not be available to update"
                        "until the error is resolved. Error is {1}".format(name, ", ".join(app_mngr.log))
                    )
                else:
                    self.app_mngrs.append(AniAppMngr(name))

        # main ui elements - styling set in the create ui functions
        self.btn_update = QtWidgets.QPushButton("Update App")
        self.btn_install = QtWidgets.QPushButton("Install / Update App(s)")
        self.btn_launch = QtWidgets.QPushButton("Launch App(s)")
        self.btn_manual_update = QtWidgets.QPushButton("Get Updates From Server Now")
        self.auto_dl_label = QtWidgets.QLabel("")
        self.menu_toggle_auto_dl = QtWidgets.QComboBox()
        self.menu_toggle_auto_dl.addItem("-------")
        self.menu_toggle_auto_dl.addItem("Enabled")
        self.menu_toggle_auto_dl.addItem("Disabled")
        # tree app version information
        self.app_tree = pyani.core.ui.CheckboxTreeWidget(self._format_app_info(), 3)

        self.create_layout()
        self.set_slots()

    def create_layout(self):

        # APP HEADER SETUP -----------------------------------
        # |    label    |   space    |     btn     |      btn       |     space    |
        g_layout_header = QtWidgets.QGridLayout()
        header_label = QtWidgets.QLabel("Applications")
        header_label.setFont(self.titles)
        g_layout_header.addWidget(header_label, 0, 0)
        g_layout_header.addItem(self.empty_space, 0, 1)
        self.btn_launch.setMinimumSize(150, 30)
        g_layout_header.addWidget(self.btn_launch, 0, 2)
        self.btn_install.setStyleSheet("background-color:{0};".format(pyani.core.ui.GREEN))
        self.btn_install.setMinimumSize(150, 30)
        g_layout_header.addWidget(self.btn_install, 0, 3)
        g_layout_header.addItem(self.empty_space, 0, 4)
        g_layout_header.setColumnStretch(1, 2)
        g_layout_header.setColumnStretch(4, 2)
        self.main_layout.addLayout(g_layout_header)
        self.main_layout.addWidget(pyani.core.ui.QHLine(pyani.core.ui.CYAN))

        # APPS TREE  -----------------------------------
        self.main_layout.addWidget(self.app_tree)

        self.main_layout.addItem(self.v_spacer)

        # MANUAL DOWNLOAD OPTIONS
        g_layout_options = QtWidgets.QGridLayout()
        options_label = QtWidgets.QLabel("Update Options")
        options_label.setFont(self.titles)
        g_layout_options.addWidget(options_label, 0, 0)
        g_layout_options.addItem(self.empty_space, 0, 1)
        self.btn_manual_update.setMinimumSize(150, 30)
        g_layout_options.addWidget(self.btn_manual_update, 0, 2)
        self.btn_manual_update.setStyleSheet("background-color:{0};".format(pyani.core.ui.GREEN))
        self.btn_manual_update.setMinimumSize(150, 30)
        g_layout_options.addItem(self.empty_space, 0, 3)
        g_layout_options.setColumnStretch(1, 2)
        self.main_layout.addLayout(g_layout_options)
        self.main_layout.addWidget(pyani.core.ui.QHLine(pyani.core.ui.CYAN))
        # set initial state of auto download based off whether
        state = self.task_scheduler.is_task_enabled()
        if not isinstance(state, bool):
            self.msg_win.show_warning_msg(
                "Task Scheduling Error",
                "Could not get state of task {0}. You can continue but you will not be "
                "able to enable or disable the windows task. Error is {1}".format(self.task_scheduler.task_name, state)
            )
        if state:
            state_label = "Enabled"
        else:
            state_label = "Disabled"
        self.auto_dl_label.setText(
            "Auto-download of updates from server <i>(Currently: {0})</i>".format(state_label)
        )
        h_options_layout = QtWidgets.QHBoxLayout()
        h_options_layout.addWidget(self.auto_dl_label)
        h_options_layout.addWidget(self.menu_toggle_auto_dl)
        h_options_layout.addStretch(1)
        self.main_layout.addLayout(h_options_layout)
        self.main_layout.addItem(self.v_spacer)

        # set main windows layout as the stacked layout
        self.add_layout_to_win()

    def set_slots(self):
        """Create the slots/actions that UI buttons / etc... do
        """
        self.btn_install.clicked.connect(self.install_apps)
        self.btn_launch.clicked.connect(self.launch)
        self.menu_toggle_auto_dl.currentIndexChanged.connect(self.update_auto_dl_state)
        self.btn_manual_update.clicked.connect(self.download_and_update)

    def update_auto_dl_state(self):
        """
        Updates a windows task in the windows task scheduler to be enabled or disabled. Informs user if can't
        set the task state
        """
        if not self.menu_toggle_auto_dl.currentIndex() == 0:
            state = self.menu_toggle_auto_dl.currentText()
            if state == "Enabled":
                error = self.task_scheduler.set_task_enabled(True)
            else:
                error = self.task_scheduler.set_task_enabled(False)
            if error:
                self.msg_win.show_warning_msg(
                    "Task Scheduling Error",
                    "Could not set state of task {0}. You can continue but you will not be "
                    "able to enable or disable the windows task. Error is {1}".format(self.task_scheduler.task_name,
                                                                                      state)
                )
                self.auto_dl_label.setText(
                    "Auto-download of updates from server <i>(Currently: Unknown)</i>"
                )
            else:
                self.auto_dl_label.setText(
                    "Auto-download of updates from server <i>(Currently: {0})</i>".format(state)
                )

    def download_and_update(self):
        """
        Calls tools setup class to download server tools package and update the local tools. If an error is
        encountered informs user.
        """
        # indicates if there is an update to install
        updates_exist = False

        self.progress_win.show_msg("Update in Progress", "Downloading Updates from Server. This file is several "
                                   "hundred megabytes (mb), please be patient.")
        QtWidgets.QApplication.processEvents()
        error = self.app_mngr.download_update()

        # not true or false, so an error occurred
        if not isinstance(error, bool):
            # done loading hide window
            self.progress_win.hide()
            QtWidgets.QApplication.processEvents()
            self.msg_win.show_error_msg("Update Failed", "Could not download update. Error is :{0}.".format(error))
            return
        # returned True, means downloaded
        elif error:
            updates_exist = True
            logging.info("App Download ran with success.")

        if updates_exist:
            self.progress_win.show_msg("Update in Progress", "Installing Updates from Server.")
            QtWidgets.QApplication.processEvents()
            error = self.app_mngr.install_update()
            if error:
                self.progress_win.hide()
                QtWidgets.QApplication.processEvents()
                self.msg_win.show_error_msg("Update Failed",
                                            "Could not install_apps update. Error is :{0}.".format(error))

            self.progress_win.hide()
            QtWidgets.QApplication.processEvents()
            self.msg_win.show_info_msg("Update Complete", "Update completed successfully!")
        else:
            self.progress_win.hide()
            QtWidgets.QApplication.processEvents()
            self.msg_win.show_info_msg("Update Complete", "No updates to download.")
            logging.info("No updates to download.")

    def install_apps(self):
        """Installs the app(s) and updates ui info. Displays install_apps errors to user.
        """
        apps = self._get_selection()
        error_log = []
        # try to install_apps selected apps, log and display error if can't and skip to next app
        for index, app in enumerate(apps):
            error = app.install()
            if error:
                error_log.append(error)
                continue
            item = [app.app_name, app.user_version, ""]
            item_color = [None, None, None]
            updated_item = pyani.core.ui.CheckboxTreeWidgetItem(item, item_color)
            self.app_tree.update_item(app.app_name, updated_item)

        if error_log:
            self.msg_win.show_error_msg("Install Error", (', '.join(error_log)))

    def launch(self):
        """Launches the app(s). Displays launch error to user
        """
        apps = self._get_selection()
        error_log = []
        for app in apps:
            exe_path = os.path.join(app.app_install_path, app.app_name)
            # pass application path and arguments, in this case none
            error = pyani.core.util.launch_app("{0}.exe".format(exe_path), [])
            if error:
                error_log.append(error)
                continue
        if error_log:
            self.msg_win.show_error_msg("App Launch Error", (', '.join(error_log)))

    def _get_selection(self):
        """
        Gets and parses the selected apps in the tree
        :return: a list of the selected tree items as AniAppMngr objects
        """
        selection = self.app_tree.get_tree_checked()
        apps = []
        # using selection, finds the app in app_mngr and adds to list
        for app_name in selection:
            for app_mngr in self.app_mngrs:
                if app_name == app_mngr.app_name:
                    apps.append(app_mngr)
        return apps

    def _format_app_info(self):
        """
        formats app information for the ui
        :return: a list of the tree information as a list of CheckboxTreeWidgetItems
        """
        tree_info = []

        if self.app_mngrs:
            # display app names, versions, and release notes if a new version is available
            for app in self.app_mngrs:
                # if users version is out of date color orange
                if not app.user_version == app.latest_version:
                    version_text = "{0}     ({1})".format(app.user_version, app.latest_version)
                    text = [app.app_name, version_text, app.features]
                    color = [pyani.core.ui.YELLOW, pyani.core.ui.YELLOW, QtCore.Qt.gray]
                    row = pyani.core.ui.CheckboxTreeWidgetItem(text, color)
                    tree_info.append({"root": row})
                # app up to date
                else:
                    text = [app.app_name, app.user_version]
                    color = None
                    row = pyani.core.ui.CheckboxTreeWidgetItem(text, color)
                    tree_info.append({"root": row})
        # problems loading app information
        else:
            text = [
                "Could not find application data. Please see log in {0}".format(self.error_logging.log_file_name), ""
            ]
            color = [pyani.core.ui.RED, pyani.core.ui.RED]
            row = pyani.core.ui.CheckboxTreeWidgetItem(text, color)
            tree_info.append({"root": row})

        return tree_info
