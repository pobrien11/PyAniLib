import logging
import os
import pyani.core.appvars
import pyani.core.util
import pyani.core.ui
import pyani.core.anivars
import pyani.render.log.data
import pyani.core.mngr.tools


# set the environment variable to use a specific wrapper
# it can be set to pyqt, pyqt5, pyside or pyside2 (not implemented yet)
# you do not need to use QtPy to set this variable
os.environ['QT_API'] = 'pyqt'
# import from QtPy instead of doing it directly
# note that QtPy always uses PyQt5 API
from qtpy import QtGui, QtWidgets, QtCore


logger = logging.getLogger()


class AniRenderDataViewer(pyani.core.ui.AniQMainWindow):
    """
    HOW IT WORKS / LOGIC:

    Start with init(), then select a sequence. Uses multi-threading for downloading and processing stats on disk.
    Calls download_sequence_stats and gets stats from CGT. Note only downloads most recent history. Checks before
    running if the data has already been loaded in by calling the render data class method is_loaded(sequence). If
    already downloaded, skips processing and just displays. Otherwise downloads and if can't download, shows message
    and loads previous seq if there was one loaded. If download successful - uses slot/signals, see
    _render_data_download_thread_complete method - calls process_sequence_stats which loads
    in the stat data from disk, and stores averages of each stat per shot/render layer/history. When complete, also uses
    slots - see _render_data_processing_thread_complete method - sets the level to sequence (i.e. 0),
    sets the nav link above graph via set_nav_link() method*, averages the sequence using render data
    class, builds render layers for sequence via private _build_render_layer_menu method, and builds the graph
    via update_ui() method.

    * set nav link creates a hyper link above graph that lets you navigate back, like bread crumbs from shot level to
      sequence level

    Clicking on a graph calls update_from_graph and shows frames if a shot was clicked, or retrieves log from cgt if
    a frame was clicked.

    Changing a stat calls update_ui() which rebuilds the graph and averages

    Changing a render layer at the sequence view calls update_ui() which rebuilds the graph and averages. Changing a
    render layer at the shot view activates history menu by calling private _build_history_menu() which
    will query cgt for that render layers history. Updates the ui menu. Then calls update_ui() which rebuilds
    the graph and averages for that render layer.

    Changing history - only available at shot level, when at the shot level and select a render layer,
    First checks if history is already processed by calling render data class method get_history() and checks if the
    history menu selection is in that list. If it is then already been processed and update_ui().
    If not downloads that render layers history stat file from CGT,
    then processes it, and finally updates the ui. If a download error occurs then does nothing.

    Clicking the seq in the nav link rebuilds history which disables it since going to sequence view,
    resets active shot rebuilds the render layer menu, rebuilds nav link, and updates_ui()


    VIEWS:

        Sequence:
            - defaults to stat totals as a sum of all render layers
            - can show stat totals per render layer - only shows shots that contain that render layer
            - displays shots as bars, these are the average a stat takes for that shot
            - sidebar average totals all shots and averages for the selected stat. Does both the stat total and
              components

        Shot:
            - defaults to stat totals as a sum of all render layers
            - displays frames as bars
            - sidebar average totals all frames and averages for the selected stat. Does both the stat total and
              components. When all render layers is selected, averages the stat for all layers
            - When all render layers is selected, the x axis frame numbers are taken from the render layer with the
              most frames. i.e. if a shot has render layers Qian and Env, Qian has frames 1001,1002, and Env has frames
              1001, then Qian will be used. The frame data for frame 1002 of env will be pulled from 1001.
            - Clicking a frame opens the log in the os default text editor

    Reports TODO:

        Show:
            - does not display per render layer, stat totals are a sum of all render layers
            - average totals all sequences and averages for the stats. Does both the stat total and
              components

    """
    def __init__(self, error_logging):
        app_name = "pyRenderDataViewer"
        app_vars = pyani.core.appvars.AppVars()
        tool_metadata = {
            "name": app_name,
            "dir": app_vars.local_pyanitools_apps_dir,
            "type": "pyanitools",
            "category": "apps"
        }
        self.tools_mngr = pyani.core.mngr.tools.AniToolsMngr()

        super(AniRenderDataViewer, self).__init__(
            "Py Render Data Viewer",
            "images\pyrenderdataviewer.png",
            tool_metadata,
            self.tools_mngr,
            1800,
            1000,
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

        self.dept = "lighting"
        self.render_data = pyani.render.log.data.AniRenderData(dept=self.dept)

        self.ani_vars = pyani.core.anivars.AniVars()
        self.app_vars = pyani.core.appvars.AppVars()
        error = self.ani_vars.load_seq_shot_list()
        if error:
            self.msg_win.show_error_msg("Critical Error", "A critical error occurred: {0}".format(error))
        else:
            # tabs class object
            self.tabs = pyani.core.ui.TabsWidget(tab_name="Stats Viewer", tabs_can_close=False)

            # tracking any download errors from cgt
            self.dl_error_list = list()

            # BEGIN VIEWER INIT ------------------------

            # threading vars
            self.thread_pool = QtCore.QThreadPool()
            logger.info("Multi-threading with maximum %d threads" % self.thread_pool.maxThreadCount())
            self.thread_total = 0.0
            self.threads_done = 0.0
            self.progress_win = QtWidgets.QProgressDialog()

            # setup color sets for the graph. Use cool colors for time stats, warm colors for memory stats, and yellow
            # for single bar stats.
            self.color_set_cool = [
                QtGui.QColor(44, 187, 162),
                QtGui.QColor(0, 160, 187),
                QtGui.QColor(51, 102, 204),
                QtGui.QColor(102, 102, 204),
                QtGui.QColor(127, 76, 159)
            ]
            self.color_set_warm = [
                QtGui.QColor(227, 192, 0),
                QtGui.QColor(227, 155, 0),
                QtGui.QColor(227, 134, 0),
                QtGui.QColor(204, 102, 0),
                QtGui.QColor(204, 51, 0),
                QtGui.QColor(204, 0, 0)
            ]

            # text font to use for ui
            self.font_family = pyani.core.ui.FONT_FAMILY
            self.font_size_menus = pyani.core.ui.FONT_SIZE_DEFAULT
            self.font_size_nav_crumbs = 11

            # load user data option
            self.load_data_btn = QtWidgets.QPushButton("Load Data")
            self.user_seq = "Seq000"
            self.user_shot = "Shot000"
            self.user_render_layer = "Render_Lyr000"

            # tracks the where we are at - sequence (display shots) or shot (displays frames)
            # current level is 0-sequence, 1-shot, corresponds as an index into self.levels list
            self.current_level = 0
            self.levels = ["Shot", "Frame"]
            self.seq = None
            self.shot = None
            # tracks the previous sequence selected when you change sequences in case we need to return due to error
            # loading data
            self.prev_seq = None

            # stats menu
            self.stats_menu = QtWidgets.QComboBox()
            for stat in self.render_data.stat_names:
                self.stats_menu.addItem(stat)
            self.selected_stat = str(self.stats_menu.currentText())

            # render layer menu - disabled until reach shot level/view
            self.render_layer = None
            self.render_layer_menu = QtWidgets.QComboBox()
            self._build_render_layer_menu()

            # history widgets - show level and sequence level only allow history="1", because doesn't make sense in
            # context of show and sequence views, since shots will have varying levels of history
            self.history_menu = QtWidgets.QComboBox()
            self._build_history_menu()
            self.history = "1"
            self.history_menu.setDisabled(True)

            # sequence selection
            self.seq_menu = QtWidgets.QComboBox()
            self.seq_menu.addItem("Select a Sequence")
            for seq in self.ani_vars.get_sequence_list():
                self.seq_menu.addItem(seq)

            self.nav_crumbs = QtWidgets.QLabel()
            self.set_nav_link()

            # averages side bar widgets created dynamically, just store layout
            self.averages_layout = QtWidgets.QVBoxLayout()

            self.bar_graph = pyani.core.ui.BarGraph()

            # override any custom qt style, which breaks the text spacing for the bar graph on axis labels
            self.bar_graph.setStyle(QtWidgets.QCommonStyle())

            # BEGIN REPORT INIT ------------------------
            _, self.show_checkbox = pyani.core.ui.build_checkbox(
                "Show", False, "Selecting Show will generate a report for all sequences."
            )
            # make sequence tree widget
            seq_tree_items = [
                {'root':pyani.core.ui.CheckboxTreeWidgetItem([seq])} for seq in self.ani_vars.get_sequence_list()
            ]
            self.sequence_tree = pyani.core.ui.CheckboxTreeWidget(seq_tree_items, columns=1)

            # make stats tree widget
            stats_tree_items = [
                {'root':pyani.core.ui.CheckboxTreeWidgetItem([stat])} for stat in self.render_data.stat_names
            ]
            self.stats_tree = pyani.core.ui.CheckboxTreeWidget(stats_tree_items, checked=True, columns=1)
            self.btn_create_report = QtWidgets.QPushButton("Generate Report")
            self.btn_create_report.setMinimumWidth(175)
            self.btn_create_report.setMinimumHeight(35)
            self.btn_create_report.setStyleSheet("background-color:{0};font-size:14pt;".format(pyani.core.ui.GREEN))
            self.progress_reports = QtWidgets.QProgressBar(self)
            self.btn_cancel_report = QtWidgets.QPushButton("Cancel Report")
            self.btn_cancel_report.setMinimumWidth(125)
            self.btn_cancel_report.setMinimumHeight(30)
            self.btn_cancel_report.setStyleSheet("background-color:{0};font-size:11pt;".format(pyani.core.ui.RED.name()))
            self.pixmap_num_1 = QtWidgets.QLabel()
            self.pixmap_num_1.setPixmap(QtGui.QPixmap("images\\number_1.png"))
            self.pixmap_num_2 = QtWidgets.QLabel()
            self.pixmap_num_2.setPixmap(QtGui.QPixmap("images\\number_2.png"))
            self.create_layout()
            self.set_slots()

    def create_layout(self):
        # add the tabs to the main layout
        self.main_layout.addWidget(self.tabs)
        # adds the viewer layout returned by create_layout_stats_viewer
        self.tabs.update_tab(self.create_layout_stats_viewer())
        # add the reports layout
        self.tabs.add_tab("Reports", self.create_layout_reports())
        self.add_layout_to_win()

    def create_layout_stats_viewer(self):
        """
        Creates the layout for the stats viewer
        :return: the layout object
        """
        main_layout = QtWidgets.QHBoxLayout()

        graph_layout = QtWidgets.QVBoxLayout()

        graph_header_layout = QtWidgets.QHBoxLayout()
        graph_header_layout.addWidget(self.nav_crumbs)
        graph_header_layout.addStretch(1)

        user_data_label = QtWidgets.QLabel(
            "<span style='font-size:{0}pt; font-family:{1}; color: #ffffff;'>User Specified Data</span>".format(
                self.font_size_menus, self.font_family
            )
        )
        graph_header_layout.addWidget(user_data_label)
        graph_header_layout.addWidget(self.load_data_btn)
        graph_header_layout.addItem(QtWidgets.QSpacerItem(75, 0))

        sequence_label = QtWidgets.QLabel(
            "<span style='font-size:{0}pt; font-family:{1}; color: #ffffff;'>Sequence</span>".format(
                self.font_size_menus, self.font_family
            )
        )
        graph_header_layout.addWidget(sequence_label)
        graph_header_layout.addWidget(self.seq_menu)
        graph_header_layout.addItem(QtWidgets.QSpacerItem(50, 0))

        history_label = QtWidgets.QLabel(
            "<span style='font-size:{0}pt; font-family:{1}; color: #ffffff;'>History</span>".format(
                self.font_size_menus, self.font_family
            )
        )
        graph_header_layout.addWidget(history_label)
        graph_header_layout.addWidget(self.history_menu)

        render_layer_label = QtWidgets.QLabel(
            "<span style='font-size:{0}pt; font-family:{1}; color: #ffffff;'>Render Layers</span>".format(
                self.font_size_menus, self.font_family
            )
        )
        graph_header_layout.addWidget(render_layer_label)
        graph_header_layout.addWidget(self.render_layer_menu)

        stats_label = QtWidgets.QLabel(
            "<span style='font-size:{0}pt; font-family:{1}; color: #ffffff;'>Render Stats</span>".format(
                self.font_size_menus, self.font_family
            )
        )
        graph_header_layout.addWidget(stats_label)
        graph_header_layout.addWidget(self.stats_menu)

        graph_layout.addLayout(graph_header_layout)
        graph_layout.addWidget(self.bar_graph)
        main_layout.addLayout(graph_layout)
        # space between graph and sidebar
        main_layout.addItem(QtWidgets.QSpacerItem(20, 0))

        # side bar gui
        main_layout.addLayout(self.averages_layout)
        # space between side bar and edge window
        main_layout.addItem(QtWidgets.QSpacerItem(20, 0))

        return main_layout

    def create_layout_reports(self):
        """
        Creates the layout for the report generator
        :return: the layout object
        """
        main_layout = QtWidgets.QVBoxLayout()

        progress_layout = QtWidgets.QHBoxLayout()
        report_progress_label = QtWidgets.QLabel(
            "<span style='font-size:11pt; font-family:{0}; color: #ffffff;'>Report Generation Progress:</span>".format(
                self.font_family
            )
        )
        progress_layout.addStretch(1)
        progress_layout.addWidget(report_progress_label)
        progress_layout.addWidget(self.progress_reports)
        progress_layout.addItem(QtWidgets.QSpacerItem(20, 0))
        progress_layout.addWidget(self.btn_cancel_report)
        main_layout.addLayout(progress_layout)

        sub_layout = QtWidgets.QHBoxLayout()

        num_1_layout = QtWidgets.QVBoxLayout()
        num_1_layout.addWidget(self.pixmap_num_1)
        num_1_layout.addStretch(1)
        sub_layout.addLayout(num_1_layout)
        sub_layout.addItem(QtWidgets.QSpacerItem(10, 0))

        view_layout = QtWidgets.QVBoxLayout()
        view_title = QtWidgets.QLabel(
            "<span style='font-size:16pt; font-family:{0}; color: #ffffff;'>Generate Report For: </span>".format(
                self.font_family
            )
        )
        view_layout.addWidget(view_title)
        show_view_layout = QtWidgets.QHBoxLayout()
        show_view_layout.addWidget(self.show_checkbox)
        show_checkbox_label = QtWidgets.QLabel(
            "<span style='font-size:11pt; font-family:{0}; color: #ffffff;'>Show </span>".format(
                self.font_family
            )
        )
        show_view_layout.addWidget(show_checkbox_label)
        show_view_layout.addStretch(1)
        view_layout.addLayout(show_view_layout)
        view_layout.addWidget(QtWidgets.QLabel("<i>This generates a report for all sequences</i>"))
        view_layout.addItem(QtWidgets.QSpacerItem(0, 20))
        seq_label = QtWidgets.QLabel(
            "<span style='font-size:11pt; font-family:{0}; color: #ffffff;'>Sequence(s) </span>".format(
                self.font_family
            )
        )
        view_layout.addWidget(seq_label)
        view_layout.addWidget(QtWidgets.QLabel("<i>This generates a report for the selected sequences</i>"))
        view_layout.addWidget(self.sequence_tree)
        sub_layout.addLayout(view_layout)

        sub_layout.addItem(QtWidgets.QSpacerItem(200, 0))

        num_2_layout = QtWidgets.QVBoxLayout()
        num_2_layout.addWidget(self.pixmap_num_2)
        num_2_layout.addStretch(1)
        sub_layout.addLayout(num_2_layout)
        sub_layout.addItem(QtWidgets.QSpacerItem(10, 0))

        stats_layout = QtWidgets.QVBoxLayout()
        stats_title = QtWidgets.QLabel(
            "<span style='font-size:16pt; font-family:{0}; color: #ffffff;'>Select Stats: </span>".format(
                self.font_family
            )
        )
        stats_layout.addWidget(stats_title)
        stats_layout.addWidget(self.stats_tree)
        sub_layout.addLayout(stats_layout)

        main_layout.addLayout(sub_layout)
        main_layout.addItem(QtWidgets.QSpacerItem(0, 50))

        btn_create_report_layout = QtWidgets.QHBoxLayout()
        btn_create_report_layout.addStretch(1)
        btn_create_report_layout.addWidget(self.btn_create_report)
        main_layout.addLayout(btn_create_report_layout)
        main_layout.addItem(QtWidgets.QSpacerItem(0, 50))

        return main_layout

    def set_slots(self):
        self.nav_crumbs.linkActivated.connect(self.update_from_nav_link)
        #self.seq_menu.currentIndexChanged.connect(self.download_sequence_stats)
        self.seq_menu.currentIndexChanged.connect(self.process_sequence_stats)
        self.history_menu.currentIndexChanged.connect(self.update_displayed_history)
        self.stats_menu.currentIndexChanged.connect(self.update_displayed_stat)
        self.render_layer_menu.currentIndexChanged.connect(self.update_displayed_render_layer)
        self.bar_graph.graph_update_signal.connect(self.update_from_graph)
        self.load_data_btn.clicked.connect(self.load_user_data)

    def load_user_data(self):
        """
        Shows render data not in the render data location on disk.
        """
        files = [
            str(filename) for filename in pyani.core.ui.FileDialog.getOpenFileNames() if str(filename).endswith("json")
        ]
        if files:
            # set the custom data in the render data object
            error = self.render_data.set_custom_data(
                files, self.user_seq, self.user_shot, self.user_render_layer
            )
            if error:
                self.msg_win.show_error_msg("Error Formatting Custom Data", error)

            # update the ui:
            #
            # set the nav link
            nav_str = "<span style='font-size:{0}pt; font-family:{1}; color: #ffffff;'>" \
                      "<b>User Custom Data</b></span></font>".format(self.font_size_nav_crumbs, self.font_family)
            self.nav_crumbs.setText(nav_str)
            # set vars
            #
            # want to be at the shot level view
            self.current_level = 1
            # These are fake seq and shot names
            self.seq = self.user_seq
            self.shot = self.user_shot
            # only show one history, no concept of history with user data
            self.history = "1"
            # rebuild history to clear any existing history
            self._build_history_menu()
            # reset render layer menu
            self._build_render_layer_menu()
            # reset sequence menu
            self.seq_menu.blockSignals(True)
            self.seq_menu.setCurrentIndex(0)
            self.seq_menu.blockSignals(False)

            # show the custom data
            self.update_ui()

    def download_shot_stats(self):
        """
        Download the a shot's stat data from CGT.
        :return True if downloaded, False if not
        """
        cgt_shot_stats_path = "/LongGong/sequences/{0}/lighting/render_data/{1}/{2}/{0}_{1}.json".format(
            self.seq,
            self.shot,
            self.history,
            self.seq,
            self.shot
        )
        # make the path to the json file, only ever one file in the history directory,
        # so we grab the first element from os.listdir
        dl_shot_stats_path = "Z:\\LongGong\\sequences\\{0}\\lighting\\render_data\\{1}\\{2}\\".format(
            self.seq,
            self.shot,
            self.history
        )
        py_script = os.path.join(self.app_vars.cgt_bridge_api_path, "cgt_download.py")
        # need to convert python lists to strings, separated by commas, so that it will pass through
        # in the shell so if there are multiple paths, the list [path1, path2] becomes 'path1,path2'
        dl_command = [
            py_script,
            cgt_shot_stats_path,
            dl_shot_stats_path,
            self.app_vars.cgt_ip,
            self.app_vars.cgt_user,
            self.app_vars.cgt_pass
        ]
        try:
            self.msg_win.show_msg("CGT Download", "Getting stat data for {0} at history: {1} from CGT, this will "
                                                  "take just a few seconds...".format(self.render_layer, self.history))
            QtWidgets.QApplication.processEvents()
            # note not using the return values, as we don't care here whether download was successful. We check below
            # for file existence
            _, _ = pyani.core.util.call_ext_py_api(dl_command)
            self.msg_win.close()
            shot_stats_path = "Z:\\LongGong\\sequences\\{0}\\lighting\\render_data\\{1}\\{2}\\{0}_{1}.json".format(
                self.seq,
                self.shot,
                self.history,
                self.seq,
                self.shot
            )

            if os.path.exists(shot_stats_path):
                stat_info = (self.seq, self.shot, self.history, self.render_layer, shot_stats_path)
                self.render_data.load_shot_stats(stat_info)
                return True
            else:
                self.msg_win.show_error_msg("Stats File Error", "Can not find stat file: {0}".format(shot_stats_path))
                return False
        except pyani.core.util.CGTError as e:
            self.msg_win.show_error_msg("CGT Download Error", "Can not download from CGT. Error is {0}".format(e))
            return False

    def download_sequence_stats_depr(self):
        """
        Download the sequences stat data from CGT using multi-threading. Uses tool managers inherited server_file_download
        function from mngr_core to download
        """
        if self.seq_menu.currentIndex() > 0:
            self.prev_seq = self.seq
            self.seq = str(self.seq_menu.currentText())
            self.ani_vars.update(seq_name=self.seq)

            # check if we need to load or its already loaded
            if self.render_data.is_loaded(self.seq):
                # no need to load, already loaded
                self.current_level = 0
                self.history = "1"
                self.set_nav_link()
                self._build_render_layer_menu()
                self.update_ui()
                return

            # reset progress
            self.progress_win.setWindowTitle("Render Data Progress")
            self.progress_win.setLabelText("Downloading {0} Render Data...".format(self.seq))
            self.progress_win.setValue(0)
            # makes sure progress shows over window, as windows os will place it under cursor behind other windows if
            # user moves mouse off off app
            self.progress_win.move(QtWidgets.QDesktopWidget().availableGeometry().center())
            self.progress_win.show()

            # make the paths to the sequence's render data
            for shot in self.ani_vars.get_shot_list():
                cgt_shot_stats_path = "/LongGong/sequences/{0}/lighting/render_data/{1}/{2}/{0}_{1}.json".format(
                    self.seq,
                    shot,
                    self.history,
                    self.seq,
                    shot
                )
                # make the path to the json file, only ever one file in the history directory,
                # so we grab the first element from os.listdir
                dl_shot_stats_path = "Z:\\LongGong\\sequences\\{0}\\lighting\\render_data\\{1}\\{2}\\".format(
                    self.seq,
                    shot,
                    self.history
                )
                py_script = os.path.join(self.app_vars.cgt_bridge_api_path, "server_download.py")
                # need to convert python lists to strings, separated by commas, so that it will pass through
                # in the shell so if there are multiple paths, the list [path1, path2] becomes 'path1,path2'
                dl_command = [
                    py_script,
                    cgt_shot_stats_path,
                    dl_shot_stats_path,
                    self.app_vars.cgt_ip,
                    self.app_vars.cgt_user,
                    self.app_vars.cgt_pass
                ]
                worker = pyani.core.ui.Worker(pyani.core.util.call_ext_py_api, False, dl_command)
                self.thread_total += 1.0
                self.thread_pool.start(worker)
                # slot that is called when a thread finishes
                worker.signals.finished.connect(self._render_data_download_thread_complete)
                worker.signals.error.connect(self._render_data_download_thread_error)

    def download_sequence_stats(self):
        """
        Download the sequences stat data from CGT using multi-threading. Uses tool managers inherited server_file_download
        function from mngr_core to download
        """
        if self.seq_menu.currentIndex() > 0:
            self._reset_thread_counters()
            self.tools_mngr.init_thread_error()

            self.prev_seq = self.seq
            self.seq = str(self.seq_menu.currentText())
            self.ani_vars.update(seq_name=self.seq)

            # check if we need to load or its already loaded
            if self.render_data.is_loaded(self.seq):
                # no need to load, already loaded
                self.current_level = 0
                self.history = "1"
                self.set_nav_link()
                self._build_render_layer_menu()
                self.update_ui()
                return

            # reset progress
            self.progress_win.setWindowTitle("Render Data Progress")
            self.progress_win.setLabelText("Downloading {0} Render Data...".format(self.seq))
            self.progress_win.setValue(0)
            # makes sure progress shows over window, as windows os will place it under cursor behind other windows if
            # user moves mouse off off app
            self.progress_win.move(QtWidgets.QDesktopWidget().availableGeometry().center())
            self.progress_win.show()

            # only connect this once - allows the download method to call its send error method which emits a
            # error_thread_signal. If we get this signal, call our custom render data thread error method. Note
            # we don't catch thread errors, but thats ok, error handling in mngr_core server download method
            self.tools_mngr.error_thread_signal.connect(self._render_data_download_thread_error)

            # make the paths to the sequence's render data
            for shot in self.ani_vars.get_shot_list():
                cgt_shot_stats_path = "/LongGong/sequences/{0}/lighting/render_data/{1}/{2}/{0}_{1}.json".format(
                    self.seq,
                    shot,
                    self.history,
                    self.seq,
                    shot
                )
                # make the path to the json file, only ever one file in the history directory,
                # so we grab the first element from os.listdir
                dl_shot_stats_path = "Z:\\LongGong\\sequences\\{0}\\lighting\\render_data\\{1}\\{2}\\".format(
                    self.seq,
                    shot,
                    self.history
                )
                worker = pyani.core.ui.Worker(
                    self.tools_mngr.server_download,
                    False,
                    [cgt_shot_stats_path],
                    local_file_paths=[dl_shot_stats_path]
                )
                self.thread_total += 1.0
                self.thread_pool.start(worker)
                # slot that is called when a thread finishes
                worker.signals.finished.connect(self._render_data_download_thread_complete)

    def process_sequence_stats(self):
        """
        Reads the sequences stat data off disk using multi-threading. If the sequence doesn't have data does nothing.
        """
        self.seq = str(self.seq_menu.currentText())
        self.ani_vars.update(seq_name=self.seq)

        # reset progress
        self.progress_win.setWindowTitle("Render Data Progress")
        self.progress_win.setLabelText("Processing {0} Render Data...".format(self.seq))
        self.progress_win.setValue(0)
        # makes sure progress shows over window, as windows os will place it under cursor behind other windows if
        # user moves mouse off off app
        self.progress_win.move(QtWidgets.QDesktopWidget().availableGeometry().center())
        self.progress_win.show()

        # make the paths to the sequence's render data
        for shot in self.ani_vars.get_shot_list():
            # make the path to the json file, only ever one file in the history directory,
            # so we grab the first element from os.listdir
            shot_stats_path = "Z:\\LongGong\\sequences\\{0}\\lighting\\render_data\\{1}\\{2}\\{0}_{1}.json".format(
                self.seq,
                shot,
                self.history,
                self.seq,
                shot
            )

            if os.path.exists(shot_stats_path):
                stat_info = (self.seq, shot, self.history, shot_stats_path)
                # creates a worker object that takes the function to run in the thread, whether to report progress,
                # and a tuple containing the sequence name, shot name, and path to the stats. Note progress
                # reporting is not progress overall, but rather the progress of the particular thread.
                # We don't need this, we just show user the overall progress, ie how many threads finished
                worker = pyani.core.ui.Worker(self.render_data.load_shot_stats, False, stat_info)
                self.thread_total += 1.0
                self.thread_pool.start(worker)
                # slot that is called when a thread finishes
                worker.signals.finished.connect(self._render_data_processing_thread_complete)

    def set_nav_link(self):
        """
        Sets the navigation link text and http link that appears above the graph. This is used to navigate up through
        the data. ie. from shot level to sequence level
        """
        if 0 <= self.current_level < len(self.levels):
            if self.current_level is 0:
                nav_str = "<span style='font-size:{0}pt; font-family:{1}; color: #ffffff;'>" \
                          "<b>{2}</b>" \
                          "</span>".format(self.font_size_nav_crumbs, self.font_family, self.seq)
            else:
                nav_str = "<span style='font-size:{0}pt; font-family:{1}; color: #ffffff;'>" \
                          "<a href='#Seq'><span style='text-decoration: none; color: #ffffff'>{2}</span></a> > " \
                          "<b>{3}</b>" \
                          "</span>".format(self.font_size_nav_crumbs, self.font_family, self.seq, self.shot)

            self.nav_crumbs.setText(nav_str)

    def update_displayed_history(self):
        """
        Updates the graph and sidebar averages with the history selected
        """
        # only allow history when viewing a shot's data
        self.history = str(self.history_menu.currentText())

        # get history if it isn't already loaded
        if self.history not in self.render_data.get_history(self.seq, self.shot, self.render_layer):
            if not self.download_shot_stats():
                # reset history
                self.history = "1"
                self.history_menu.blockSignals(True)
                self.history_menu.setCurrentIndex(0)
                self.history_menu.blockSignals(False)
        self.update_ui()

    def update_displayed_render_layer(self):
        """
        Updates the graph and sidebar averages with the render layer selected
        """
        # only allow render layer when viewing a shot's data
        self.render_layer = str(self.render_layer_menu.currentText())
        # only build history when at shot level
        if self.seq and self.shot:
            self._build_history_menu()
        self.update_ui()

    def update_displayed_stat(self):
        """
        Updates the graph and sidebar averages with the render stat selected
        """
        # always set to selection, even if no data loaded, allows you to start with a different stat than first one
        # listed in the menu
        self.selected_stat = str(self.stats_menu.currentText())
        # only process if a sequence has been selected and loaded
        if self.seq:
            self.update_ui()

    def update_from_graph(self, x_axis_value):
        """
        Takes a x axis value clicked on and updates the ui and graph. Clicking the graph allows you to dive further into
        the data, ie go from show view to sequence view to shot view
        :param x_axis_value: the x axis value clicked on from the graph = gets via signal slot,
        see pyani.core.ui.BarGraph class
        """
        # don't do anything if custom data loaded
        if self.seq == self.user_seq:
            return

        # set the level based off x axis value clicked on
        if pyani.core.util.is_valid_seq_name(str(x_axis_value)):
            self.seq = str(x_axis_value)
            self._build_render_layer_menu()
            self.current_level = 0
        # build shot view, note we don't do history here because we start with the all render layers. history gets
        # built when a render layer is selected
        elif pyani.core.util.is_valid_shot_name(str(x_axis_value)):
            self.history_menu.setDisabled(False)
            self.shot = str(x_axis_value)
            self.current_level = 1
            self._build_render_layer_menu()
        elif pyani.core.util.is_valid_frame(str(x_axis_value)):
            # no more levels, don't do anything except show log
            self.get_log(str(x_axis_value))
        else:
            # don't know what it is, display warning and don't change level
            self.msg_win.show_warning_msg(
                "Warning", "Could not update graph. {0} is not a valid x axis value".format(str(x_axis_value))
            )

        self.set_nav_link()
        self.update_ui()

    def update_from_nav_link(self, link):
        """
        Updates the graph and sidebar averages based off the navigation text that was clicked
        :param link: passed from the signal connected to the text
        """
        if link == "#Seq":
            # back to sequence level, reset shot to none
            self.shot = None
            # disable and reset render layer and history menus since not at shot level
            self._build_history_menu()
            # at sequence level so don't show history
            self.history_menu.setDisabled(True)
            # rebuild the menu to get render layers for sequence
            self._build_render_layer_menu()
            self.current_level = 0
        self.set_nav_link()
        self.update_ui()

    def update_ui(self):
        """
        Updates the data based off the ui selections, and passes to graph. Also updates sidebar averages
        :return:
        """
        # process the render data based off shot level
        if self.current_level is 1:
            # check if the menu is the first entry, which is all render layers, if so process all render layers
            # in the shot
            if self.render_layer_menu.currentIndex() == 0:
                # process data for every render layer
                for render_layer in self.render_data.get_render_layers(self.seq, self.shot, history=self.history):
                    self.render_data.process_data(
                        self.selected_stat, self.seq, self.shot, render_layer, history=self.history
                    )
            else:
                self.render_data.process_data(
                    self.selected_stat, self.seq, self.shot, self.render_layer, history=self.history
                )
        else:
            # process the render data based off seq level
            self.render_data.process_data(self.selected_stat, self.seq)

        # rebuild data
        graph_data = self.build_graph_data()
        # check if render data was found - build_graph_data returns a tuple of labels, data, color. if it's not a tuple
        # show error and exit
        if not isinstance(graph_data, tuple):
            self.msg_win.show_error_msg("No render data", "Could not find any render data for {0}".format(self.seq))
            return
        else:
            x_axis_labels, graph_data, colors = graph_data
        # get the label for the y axis
        if "min" in self.render_data.get_stat_type(self.selected_stat):
            y_label = "minutes (min)"
        elif "gb" in self.render_data.get_stat_type(self.selected_stat):
            y_label = "gigabytes (gb)"
        else:
            y_label = "percent (%)"

        self.bar_graph.update_graph(
            x_axis_label=self.levels[self.current_level],
            y_axis_label=y_label,
            x_data=x_axis_labels,
            y_data=graph_data,
            width=0.95,
            color=colors
        )
        # rebuild sidebar averages ui
        self.build_averages_sidebar()

    def build_graph_data(self):
        """
        Makes the data dict and color dict needed by the bar graph
        :return: The x axis labels, the data dict of float data and the color dict of pyqt colors
        corresponding to the data. If there isn't render data, then returns None
        """
        # colors for the main stat number i.e. the total
        colors = {
            'total': QtGui.QColor(100, 100, 100),
            'components': []
        }

        # the data we pass to the bar graph, expects the format:
        # { 'total': [;list of floats], 'components':[nested list of floats, where each element is a frame, shot, or
        # sequence of data] }
        graph_data = {}
        graph_data['total'] = []
        graph_data['components'] = []

        # build data for sequence
        if self.current_level is 0:
            # check if the menu is the first entry, which is all render layers
            if self.render_layer_menu.currentIndex() == 0:
                x_axis_labels = self.render_data.get_shots(self.seq)
                for shot in x_axis_labels:
                    # sum of stat for all render layers
                    main_total, component_totals = self.render_data.get_average(self.selected_stat, self.seq, shot)
                    graph_data['total'].append(main_total)
                    graph_data['components'].append(component_totals)
            # specific render layer selected
            else:
                x_axis_labels = self.render_data.get_shots(self.seq, render_layer=self.render_layer)
                for shot in x_axis_labels:
                    main_total, component_totals = self.render_data.get_average(
                        self.selected_stat, self.seq, shot, self.render_layer, self.history
                    )
                    graph_data['total'].append(main_total)
                    graph_data['components'].append(component_totals)

        # build data for shot
        else:
            x_axis_labels = []
            # check if the menu is the first entry, which is all render layers, if so find the render layer
            # with the most frames, and use that as the x axis label
            if self.render_layer_menu.currentIndex() == 0:
                # get the render layer with the most frames
                for render_layer in self.render_data.get_render_layers(self.seq, self.shot, history=self.history):
                    frames = self.render_data.get_frames(self.seq, self.shot, render_layer, history=self.history)
                    if len(frames) > len(x_axis_labels):
                        x_axis_labels = frames
            else:
                x_axis_labels = self.render_data.get_frames(
                    self.seq, self.shot, self.render_layer, history=self.history
                )

            # for every frame build data
            for frame in x_axis_labels:
                # check if the menu is the first entry, which is all render layers, if so process all render layers
                # in the shot
                if self.render_layer_menu.currentIndex() == 0:
                    total = 0.0
                    component_total = [0.0] * len(self.render_data.get_stat_components(self.selected_stat))
                    render_layers = self.render_data.get_render_layers(self.seq, self.shot, history=self.history)
                    for render_layer in render_layers:
                        # need to add up the stat for every render layer - note frame data may not exist, since we
                        # use the frame count from the render layer that has the most frames. If the data isn't there,
                        # use the first existing frame's data
                        if frame not in self.render_data.stat_data[self.seq][self.shot][render_layer][self.history]:
                            # take the first frame's value
                            frame = self.render_data.get_frames(self.seq, self.shot, render_layer, history=self.history)[0]

                        frame_total, frame_component_totals = self.render_data.get_totals(
                            self.selected_stat, self.seq, self.shot, render_layer, frame, self.history
                        )
                        total += frame_total
                        for i, component in enumerate(frame_component_totals):
                            component_total[i] += component

                    # treat cpu utilization specially, because just summing it doesn't make much sense. Consider
                    # render layer 1 uses 93%, and render layer 2 is 90%, seeing 183% doesn't really help. Averaging
                    # is a slightly better help
                    if "cpu utilization" in self.selected_stat:
                        total /= len(render_layers)

                    graph_data['total'].append(total)
                    graph_data['components'].append(component_total)
                else:
                    frame_total, frame_component_totals = self.render_data.get_totals(
                        self.selected_stat, self.seq, self.shot, self.render_layer, frame, self.history
                    )
                    graph_data['total'].append(frame_total)
                    graph_data['components'].append(frame_component_totals)

        # find color set to use
        if graph_data['total']:
            if self.render_data.get_stat_type(self.selected_stat) == "min":
                colors_to_use = self.color_set_cool
            elif self.render_data.get_stat_type(self.selected_stat) == 'gb':
                colors_to_use = self.color_set_warm
            else:
                colors_to_use = None
                colors['total'] = pyani.core.ui.YELLOW
            # set colors for the components
            for index in xrange(0, len(graph_data['components'][0])):
                colors['components'].append(colors_to_use[index])

            return x_axis_labels, graph_data, colors
        else:
            return None

    def build_averages_sidebar(self):
        """
        Makes the side bar that displays averages for the stats. The sidebar lists the main stat first, then
        any sub-component averages
        """
        # clear side bar layout
        pyani.core.ui.clear_layout(self.averages_layout)

        # push sidebar down
        self.averages_layout.addItem(QtWidgets.QSpacerItem(0, 75))

        # figure out if its measured in time, size, or percent
        stat_type = self.render_data.get_stat_type(self.selected_stat)

        # set the color set based off stat type
        if stat_type is 'gb':
            color_set = self.color_set_warm
        else:
            color_set = self.color_set_cool

        # average based off level - ie sequence, shot, or frame
        if self.levels[self.current_level] == "Frame":
            # check if the menu is the first entry, which is all render layers, if so process all render layers
            # in the shot
            if self.render_layer_menu.currentIndex() == 0:
                # average stat for all the frames of all render layers in this shot - can just grab the shot average
                # since its already an average of the stat for all render layers for every frame in the shot. Need
                # to build the data first though
                self.render_data.process_data(self.selected_stat, self.seq, self.shot)
                main_total, component_totals = self.render_data.get_average(self.selected_stat, self.seq, self.shot)
            else:
                # average stat for all the frames of a single render layer in this shot
                main_total, component_totals = self.render_data.get_average(
                    self.selected_stat, self.seq, self.shot, self.render_layer, self.history
                )
        else:
            # check if the menu is the first entry, which is all render layers, if so process all render layers
            # in the shot
            if self.render_layer_menu.currentIndex() == 0:
                # get average for stat for all shots in sequence
                main_total, component_totals = self.render_data.get_average(self.selected_stat, self.seq)
            else:
                # get average for stat for shots that have the specified render layer
                main_total, component_totals = self.render_data.get_average(
                    self.selected_stat, self.seq, render_layer=self.render_layer
                )

        # format the total - the average followed by the type such as seconds. Then add a '/' followed by level
        # examples: 25.5s / frame or 30gb / shot
        average_total = QtWidgets.QLabel()
        average_total.setText(
            "<span style='font-size:30pt; font-family:{3};'><b>{0:.2f}</span></b>"
            "<span style='font-size:12pt; font-family:{3};'> {1} / {2}</span>"
                .format(main_total, stat_type, self.levels[self.current_level], self.font_family)
        )
        # add subtitle displaying stat name
        average_total_subtitle = QtWidgets.QLabel()
        average_total_subtitle.setText(
            "<span style='font-size:12pt; font-family:{1};'><b>{0}</b></span>".format(
                self.selected_stat, self.font_family
            )
        )
        average_total_subtitle.setAlignment(QtCore.Qt.AlignCenter)
        self.averages_layout.addWidget(average_total)
        self.averages_layout.addWidget(average_total_subtitle)
        self.averages_layout.addItem(QtWidgets.QSpacerItem(0, 50))

        # now add any components, and format the same as the main total
        for index, component_name in enumerate(self.render_data.get_stat_components(self.selected_stat)):
            label = QtWidgets.QLabel()
            label.setText(
                "<span style='font-size:25pt; font-family:{3};'><b>{0:.2f}</span></b>"
                "<span style='font-size:12pt; font-family:{3};'> {1} / {2}</span>"
                    .format(component_totals[index], stat_type, self.levels[self.current_level], self.font_family)
            )
            label_subtitle = QtWidgets.QLabel()
            label_subtitle.setText(
                "<span style='font-size:12pt; color:{0}; font-family:{2};'><b>{1}</b></span>"
                    .format(color_set[index].name(), component_name, self.font_family)
            )
            label_subtitle.setAlignment(QtCore.Qt.AlignCenter)
            self.averages_layout.addWidget(label)
            self.averages_layout.addWidget(label_subtitle)
            self.averages_layout.addItem(QtWidgets.QSpacerItem(0, 15))
        self.averages_layout.addStretch(1)

    def get_log(self, frame):
        """
        Gets the log for the frame clicked on in the graph and opens in the system's default text editor. Typically
        notepad on windows
        :param: frame: the frame number clicked on as a string
        """
        # don't do anything if custom data loaded
        if self.seq == self.user_seq:
            return

        if self.render_layer_menu.currentIndex() == 0:
            self.msg_win.show_info_msg("Unsupported Log Selection", "Select a render layer in the render layer menu, "
                                                                    "then click on a frame to load the log.")
            return

        self.msg_win.show_msg("Opening Log", "Downloading log...This will close once the log is downloaded and the "
                                             "log will open in the default system text editor")
        QtWidgets.QApplication.processEvents()
        app_vars = pyani.core.appvars.AppVars()
        py_script = os.path.join(app_vars.cgt_bridge_api_path, "cgt_download.py")
        # cgt path
        log_cgt_path = r"/LongGong/sequences/{0}/{1}/{2}/render_data/{3}/{4}/{5}_{6}_{7}.{8}.log".format(
                    self.seq,
                    self.shot,
                    self.dept,
                    self.render_layer,
                    self.history,
                    self.seq,
                    self.shot,
                    self.render_layer,
                    frame
                )
        # download location for log
        log_dl_path = r"Z:\LongGong\sequences\{0}\{1}\{2}\render_data\{3}\{4}".format(
                    self.seq,
                    self.shot,
                    self.dept,
                    self.render_layer,
                    self.history
                )
        # the full path to the log on disk
        downloaded_log = r"Z:\LongGong\sequences\{0}\{1}\{2}\render_data\{3}\{4}\{5}_{6}_{7}.{8}.log".format(
                self.seq,
                self.shot,
                self.dept,
                self.render_layer,
                self.history,
                self.seq,
                self.shot,
                self.render_layer,
                frame
            )
        # download command
        dl_command = [
            py_script,
            log_cgt_path,
            log_dl_path,
            app_vars.cgt_ip,
            app_vars.cgt_user,
            app_vars.cgt_pass
        ]
        try:
            output, error = pyani.core.util.call_ext_py_api(dl_command)
            # close the info msg
            self.msg_win.close()
            # error from trying to open subprocess
            if error:
                self.msg_win.show_error_msg("Log Download Error", "Encountered an error opening cgt python script."
                                                                  "Error is {0}".format(error)
                                            )
                return
            # opens the default text editor
            os.startfile(downloaded_log)

        except pyani.core.util.CGTError as e:
            # close the info msg
            self.msg_win.close()
            self.msg_win.show_error_msg("Log Download Error", "Encountered an error downloading the log. Check if the "
                                                              "log exists in CGT and you are connected to the VPN. "
                                                              "Error is {0}".format(e)
                                        )
            return

    def _render_data_processing_thread_complete(self):
        """
        Called when a thread that processes render data for a shot completes. When all threads complete, refreshes
        the ui
        """
        self.threads_done += 1.0
        progress = (self.threads_done / self.thread_total) * 100.0
        self.progress_win.setValue(progress)
        if progress >= 100.0:
            # reset threads counters
            self.thread_total = 0.0
            self.threads_done = 0.0

            self.current_level = 0
            self.set_nav_link()
            self.render_data.process_data(self.render_data.stat_data, seq=self.seq)
            self._build_render_layer_menu()
            self.update_ui()

    def _render_data_download_thread_complete(self):
        """
        Called when a thread that downloads render data for a shot completes. When all threads complete, processes
        the data
        """
        self.threads_done += 1.0
        progress = (self.threads_done / self.thread_total) * 100.0
        self.progress_win.setValue(progress)
        if progress >= 100.0:
            # check for errors - first check if error list is the same number as thread total then nothing downloaded
            if len(self.dl_error_list) >= self.thread_total:
                self.msg_win.show_error_msg("CGT Download Error", "Could not download render stats. "
                                                                  "See log for details.")
                logger.error("download errors from cgt: " + ', '.join(self.dl_error_list))
                # reset sequence - if one was loaded
                if self.prev_seq:
                    self.seq = self.prev_seq
                    self.shot = None
                    self.seq_menu.setCurrentIndex(self.seq_menu.findText(self.seq))
                    self.ani_vars.update(seq_name=self.seq)
                    self.current_level = 0
                    self.history = "1"
                    self.set_nav_link()
                    self._build_render_layer_menu()
                    self.update_ui()
                # reset d/l error list
                self.dl_error_list = []
                return
            # some files downloaded, but not all
            elif self.dl_error_list:
                self.msg_win.show_error_msg("CGT Download Error", "({0}) shot's render data could not be downloaded. "
                                                                  "See log for details.".format(len(self.dl_error_list))
                                            )
                logger.error("download errors from cgt: " + ', '.join(self.dl_error_list))
                # reset d/l error list
                self.dl_error_list = []

            self.process_sequence_stats()

    def _render_data_download_thread_error(self, error):
        """
        Called when an error occurs in from downloading
        :param error: a string error
        """
        # record error in a list, when all threads complete can use this to inform user
        self.dl_error_list.append(str(error))

    def _build_render_layer_menu(self):
        """
        Builds the render layer menu, based off the sequence, shot and history. Adds an "All Render Layers" option
        that calculates all render layer's stats together. This is the default.
        """
        # block signals so currentIndexChanged doesn't get invoked when we clear and rebuild menu
        self.render_layer_menu.blockSignals(True)
        self.render_layer_menu.clear()
        # a seq and shot are set and not on a custom user loaded sequence
        if (self.seq and self.shot) and (not self.seq == self.user_seq):
            # add the all render layers option
            self.render_layer_menu.addItem("All Render Layers")
            for render_layer in self.render_data.get_render_layers(self.seq, self.shot, history="1"):
                self.render_layer_menu.addItem(render_layer)
        # a seq is set but no shot and not on a custom user loaded sequence
        elif (self.seq and not self.shot) and (not self.seq == self.user_seq):
            # add the all render layers option
            self.render_layer_menu.addItem("All Render Layers")
            for render_layer in self.render_data.get_render_layers(self.seq, history="1"):
                self.render_layer_menu.addItem(render_layer)
        else:
            self.render_layer_menu.addItem("N/A : Please select a shot")
        # set the active render layer to the first menu item as a default
        self.render_layer = str(self.render_layer_menu.currentText())
        self.render_layer_menu.blockSignals(False)

    def _build_history_menu(self):
        """
        Makes the history menu based off the current seq and shot. If no seq or shot is set, defaults to
        "1". Also defaults to "1" if user data is loaded (the sequence is set to the user_seq member var. Finally
        history is set to "1" when the user is displaying all render layers instead of a specific render layer
        """
        # block signals so currentIndexChanged doesn't get invoked when we clear and rebuild menu
        self.history_menu.blockSignals(True)
        self.history_menu.clear()
        # ensures the seq and shot were set and not on a custom user loaded sequence and also checks 'all render layers'
        # isn't selected. Doesn't make sense to build history when all render layers are being totaled.
        if (self.seq and self.shot) and (not self.seq == self.user_seq) and \
                int(self.render_layer_menu.currentIndex()) > 0:

            self.msg_win.show_msg("CGT Download", "Getting history from CGT, this will take just a few seconds...")
            QtWidgets.QApplication.processEvents()
            # need to get history from cgt
            cgt_shot_history = "/LongGong/sequences/{0}/lighting/render_data/{1}/".format(
                self.seq,
                self.shot
            )
            py_script = os.path.join(self.app_vars.cgt_bridge_api_path, "cgt_download.py")
            # need to convert python lists to strings, separated by commas, so that it will pass through
            # in the shell so if there are multiple paths, the list [path1, path2] becomes 'path1,path2'
            dl_command = [
                py_script,
                cgt_shot_history,
                "",
                self.app_vars.cgt_ip,
                self.app_vars.cgt_user,
                self.app_vars.cgt_pass,
                "True"
            ]
            output, error = pyani.core.util.call_ext_py_api(dl_command)
            self.msg_win.close()
            if output:
                history_paths = output.split(",")
                history_list = [
                    history_path.split("/")[-1].replace("\n", "").replace("\r", "") for history_path in history_paths
                ]
                for history in sorted(history_list):
                    # only add history folders
                    if pyani.core.util.is_number(history):
                        self.history_menu.addItem(history)
            else:
                self.history_menu.addItem("1")
        else:
            self.history_menu.addItem("1")
        # reset the current history
        self.history = str(self.history_menu.currentText())
        self.history_menu.blockSignals(False)

    def _reset_thread_counters(self):
        """
        resets the thread counters to zero
        """
        # reset threads counters
        self.thread_total = 0.0
        self.threads_done = 0.0