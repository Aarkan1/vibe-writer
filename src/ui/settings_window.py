import os
import sys
from dotenv import set_key, load_dotenv
from PyQt5.QtWidgets import (
    QApplication, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
    QMessageBox, QTabWidget, QWidget, QSizePolicy, QSpacerItem, QToolButton, QStyle, QFileDialog,
    QTextEdit
)
from PyQt5.QtCore import Qt, QCoreApplication, QProcess, pyqtSignal
from PyQt5.QtGui import QPainter, QBrush, QColor, QPen

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ui.base_window import BaseWindow
from utils import ConfigManager

load_dotenv()

class SettingsWindow(BaseWindow):
    settings_closed = pyqtSignal()
    settings_saved = pyqtSignal()

    def __init__(self):
        """Initialize the settings window."""
        super().__init__('Settings', 700, 700)
        self.schema = ConfigManager.get_schema()
        self.init_settings_ui()
        # Apply dark theme to match PromptPopup styling
        self._apply_dark_theme_styles()

    def init_settings_ui(self):
        """Initialize the settings user interface."""
        self.tabs = QTabWidget()
        self.main_layout.addWidget(self.tabs)

        self.create_tabs()
        self.create_buttons()

        # Connect the use_api checkbox state change
        self.use_api_checkbox = self.findChild(QCheckBox, 'model_options_use_api_input')
        if self.use_api_checkbox:
            self.use_api_checkbox.stateChanged.connect(lambda: self.toggle_api_local_options(self.use_api_checkbox.isChecked()))
            self.toggle_api_local_options(self.use_api_checkbox.isChecked())

    def create_tabs(self):
        """Create tabs for each category in the schema."""
        for category, settings in self.schema.items():
            tab = QWidget()
            tab_layout = QVBoxLayout()
            tab.setLayout(tab_layout)
            self.tabs.addTab(tab, category.replace('_', ' ').capitalize())

            self.create_settings_widgets(tab_layout, category, settings)
            tab_layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

    def create_settings_widgets(self, layout, category, settings):
        """Create widgets for each setting in a category."""
        for sub_category, sub_settings in settings.items():
            if isinstance(sub_settings, dict) and 'value' in sub_settings:
                self.add_setting_widget(layout, sub_category, sub_settings, category)
            else:
                for key, meta in sub_settings.items():
                    self.add_setting_widget(layout, key, meta, category, sub_category)

    def create_buttons(self):
        """Create reset and save buttons."""
        reset_button = QPushButton('Reset to saved settings')
        reset_button.clicked.connect(self.reset_settings)
        self.main_layout.addWidget(reset_button)

        save_button = QPushButton('Save')
        save_button.clicked.connect(self.save_settings)
        self.main_layout.addWidget(save_button)

    def add_setting_widget(self, layout, key, meta, category, sub_category=None):
        """Add a setting widget to the layout."""
        item_layout = QHBoxLayout()
        label = QLabel(f"{key.replace('_', ' ').capitalize()}:")
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        widget = self.create_widget_for_type(key, meta, category, sub_category)
        if not widget:
            return

        help_button = self.create_help_button(meta.get('description', ''))

        item_layout.addWidget(label)
        if isinstance(widget, QWidget):
            item_layout.addWidget(widget)
        else:
            item_layout.addLayout(widget)
        item_layout.addWidget(help_button)
        layout.addLayout(item_layout)

        # Set object names for the widget, label, and help button
        widget_name = f"{category}_{sub_category}_{key}_input" if sub_category else f"{category}_{key}_input"
        label_name = f"{category}_{sub_category}_{key}_label" if sub_category else f"{category}_{key}_label"
        help_name = f"{category}_{sub_category}_{key}_help" if sub_category else f"{category}_{key}_help"
        
        label.setObjectName(label_name)
        help_button.setObjectName(help_name)
        
        if isinstance(widget, QWidget):
            widget.setObjectName(widget_name)
        else:
            # If it's a layout (for model_path), set the object name on the QLineEdit
            line_edit = widget.itemAt(0).widget()
            if isinstance(line_edit, QLineEdit):
                line_edit.setObjectName(widget_name)

    def create_widget_for_type(self, key, meta, category, sub_category):
        """Create a widget based on the meta type."""
        meta_type = meta.get('type')
        current_value = self.get_config_value(category, sub_category, key, meta)

        if meta_type == 'bool':
            return self.create_checkbox(current_value, key)
        elif meta_type == 'str' and 'options' in meta:
            # Make openrouter.model and openai.model editable so users can type any model name, while keeping dropdown suggestions.
            editable = ((category == 'openrouter' or category == 'openai') and key == 'model')
            return self.create_combobox(current_value, meta['options'], editable)
        elif meta_type == 'str':
            return self.create_line_edit(current_value, key, category, sub_category)
        elif meta_type == 'text':
            return self.create_text_edit(current_value)
        elif meta_type in ['int', 'float']:
            return self.create_line_edit(str(current_value))
        return None

    def create_checkbox(self, value, key):
        widget = QCheckBox()
        widget.setChecked(value)
        if key == 'use_api':
            widget.setObjectName('model_options_use_api_input')
        return widget

    def create_combobox(self, value, options, editable=False):
        widget = QComboBox()
        widget.setEditable(editable)  # Allow free text for openrouter.model only
        widget.addItems(options)
        widget.setCurrentText(value)
        return widget
    
    def create_line_edit(self, value, key=None, category=None, sub_category=None):
        widget = QLineEdit(value)
        if key == 'api_key':
            widget.setEchoMode(QLineEdit.Password)
            # Prefill from appropriate env var so saved keys reappear masked
            env_value = None
            if category == 'model_options' and sub_category == 'api':
                env_value = os.getenv('OPENAI_API_KEY')
            elif category == 'openrouter':
                env_value = os.getenv('OPENROUTER_API_KEY')
            elif category == 'openai':
                env_value = os.getenv('OPENAI_API_KEY')
            if env_value:
                widget.setText(env_value)
        elif key == 'model_path':
            layout = QHBoxLayout()
            layout.addWidget(widget)
            browse_button = QPushButton('Browse')
            browse_button.clicked.connect(lambda: self.browse_model_path(widget))
            layout.addWidget(browse_button)
            layout.setContentsMargins(0, 0, 0, 0)
            container = QWidget()
            container.setLayout(layout)
            return container
        return widget

    def create_text_edit(self, value):
        """Create a multi-line text area for long prompt templates."""
        widget = QTextEdit()
        widget.setPlainText(str(value) if value is not None else '')
        widget.setAcceptRichText(False)
        widget.setMinimumHeight(120)
        return widget

    def create_help_button(self, description):
        help_button = QToolButton()
        help_button.setIcon(self.style().standardIcon(QStyle.SP_MessageBoxQuestion))
        help_button.setAutoRaise(True)
        help_button.setToolTip(description)
        help_button.setCursor(Qt.PointingHandCursor)
        help_button.setFocusPolicy(Qt.TabFocus)
        help_button.clicked.connect(lambda: self.show_description(description))
        return help_button

    def get_config_value(self, category, sub_category, key, meta):
        if sub_category:
            return ConfigManager.get_config_value(category, sub_category, key) or meta['value']
        return ConfigManager.get_config_value(category, key) or meta['value']

    def browse_model_path(self, widget):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Whisper Model File", "", "Model Files (*.bin);;All Files (*)")
        if file_path:
            widget.setText(file_path)

    def show_description(self, description):
        """Show a description dialog."""
        QMessageBox.information(self, 'Description', description)

    # ------------------------- Theming & Painting ------------------------- #
    def paintEvent(self, _):
        """Draw a dark rounded background with a subtle border, matching popup."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bg = QColor(15, 17, 21, 235)
        border = QColor(58, 64, 72, 200)
        rect = self.rect().adjusted(0, 0, -1, -1)
        radius = 14
        painter.setPen(QPen(border, 1))
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(rect, radius, radius)

    def _apply_dark_theme_styles(self):
        """Apply dark theme QSS consistent with the popup window.

        This includes inputs, tabs, buttons, labels, checkboxes, scrollbars, and combo boxes.
        It also restyles the BaseWindow title and close button to ensure contrast.
        """
        scrollbar_qss = (
            "QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }"
            " QScrollBar::handle:vertical { background: rgba(255,255,255,0.16); min-height: 24px; border-radius: 5px; }"
            " QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.26); }"
            " QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
            " QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
            " QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }"
            " QScrollBar::handle:horizontal { background: rgba(255,255,255,0.16); min-width: 24px; border-radius: 5px; }"
            " QScrollBar::handle:horizontal:hover { background: rgba(255,255,255,0.26); }"
            " QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }"
            " QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }"
        )

        self.setStyleSheet(
            # Base text color
            "QWidget { color: #E8EAED; background: transparent; }"
            # Labels
            " QLabel { color: #DDE2E7; font-size: 12px; }"
            # Line edits
            " QLineEdit { background: rgba(255,255,255,0.04); color: #E8EAED; border: 1px solid #3A4048; border-radius: 8px; padding: 6px 8px; }"
            " QLineEdit:focus { border-color: #4A90E2; }"
            # Text edits
            " QTextEdit { background: rgba(255,255,255,0.04); color: #E8EAED; border: 1px solid #3A4048; border-radius: 8px; padding: 6px; }"
            # Combo boxes
            " QComboBox { background: rgba(255,255,255,0.04); color: #E8EAED; border: 1px solid #3A4048; border-radius: 8px; padding: 4px 8px; }"
            " QComboBox:hover { border-color: #4A90E2; }"
            " QComboBox QAbstractItemView { background: #1E2228; color: #E8EAED; border: 1px solid #3A4048; selection-background-color: rgba(255,255,255,0.10); }"
            # Buttons
            " QPushButton { color: #B5B9C0; background: rgba(255,255,255,0.06); border: 1px solid #3A4048; border-radius: 8px; padding: 6px 10px; font-size: 12px; }"
            " QPushButton:hover { background: rgba(255,255,255,0.10); }"
            " QPushButton:pressed { background: rgba(255,255,255,0.12); }"
            # Tool buttons (help icons)
            " QToolButton { color: #DDE2E7; background: transparent; border: none; }"
            # Checkboxes
            " QCheckBox { color: #DDE2E7; font-size: 12px; }"
            " QCheckBox::indicator { width: 14px; height: 14px; }"
            " QCheckBox::indicator:unchecked { border: 1px solid #3A4048; background: rgba(255,255,255,0.02); border-radius: 3px; }"
            " QCheckBox::indicator:checked { border: 1px solid #4A90E2; background: #4A90E2; }"
            # Tabs
            " QTabWidget::pane { border: 1px solid #2E333B; border-radius: 8px; top: -1px; }"
            " QTabBar::tab { background: rgba(255,255,255,0.04); color: #E8EAED; border: 1px solid #2E333B; border-bottom: none; padding: 6px 10px; min-width: 100px; }"
            " QTabBar::tab:first { border-top-left-radius: 8px; }"
            " QTabBar::tab:last { border-top-right-radius: 8px; }"
            " QTabBar::tab:selected { background: rgba(255,255,255,0.10); }"
            " QTabBar::tab:hover { background: rgba(255,255,255,0.08); }"
            + scrollbar_qss
        )

        # Restyle BaseWindow title label and close button for contrast
        try:
            for lbl in self.findChildren(QLabel):
                if (lbl.text() or '').strip() == 'VibeWriter':
                    lbl.setStyleSheet("color: #DDE2E7; font-size: 12px; font-weight: 600;")
                    break
        except Exception:
            pass
        try:
            for btn in self.findChildren(QPushButton):
                if (btn.text() or '').strip() == '×':
                    btn.setStyleSheet(
                        "QPushButton { background-color: transparent; border: none; color: #DDE2E7; }"
                        " QPushButton:hover { color: #FFFFFF; }"
                    )
                    break
        except Exception:
            pass

    def save_settings(self):
        """Save the settings to the config file and .env file."""
        self.iterate_settings(self.save_setting)

        # Resolve absolute .env path at project root to ensure consistent writes
        env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

        # Save the OpenAI API key(s) to the .env file
        openai_api_key = (
            ConfigManager.get_config_value('openai', 'api_key')
            or ConfigManager.get_config_value('model_options', 'api', 'api_key')
            or ''
        )
        if openai_api_key:
            set_key(env_path, 'OPENAI_API_KEY', openai_api_key)
            os.environ['OPENAI_API_KEY'] = openai_api_key
            # Remove copies from configs
            ConfigManager.set_config_value(None, 'openai', 'api_key')
            ConfigManager.set_config_value(None, 'model_options', 'api', 'api_key')

        # Save the OpenRouter API key to the .env file
        openrouter_api_key = ConfigManager.get_config_value('openrouter', 'api_key') or ''
        if openrouter_api_key:
            set_key(env_path, 'OPENROUTER_API_KEY', openrouter_api_key)
            os.environ['OPENROUTER_API_KEY'] = openrouter_api_key
            # Remove it from the config file
            ConfigManager.set_config_value(None, 'openrouter', 'api_key')

        ConfigManager.save_config()
        QMessageBox.information(self, 'Settings Saved', 'Settings have been saved. The application will now restart.')
        self.settings_saved.emit()
        self.close()

    def save_setting(self, widget, category, sub_category, key, meta):
        value = self.get_widget_value_typed(widget, meta.get('type'))
        if sub_category:
            ConfigManager.set_config_value(value, category, sub_category, key)
        else:
            ConfigManager.set_config_value(value, category, key)

    def reset_settings(self):
        """Reset the settings to the saved values."""
        ConfigManager.reload_config()
        self.update_widgets_from_config()

    def update_widgets_from_config(self):
        """Update all widgets with values from the current configuration."""
        self.iterate_settings(self.update_widget_value)

    def update_widget_value(self, widget, category, sub_category, key, meta):
        """Update a single widget with the value from the configuration."""
        if sub_category:
            config_value = ConfigManager.get_config_value(category, sub_category, key)
        else:
            config_value = ConfigManager.get_config_value(category, key)

        self.set_widget_value(widget, config_value, meta.get('type'))

    def set_widget_value(self, widget, value, value_type):
        """Set the value of the widget."""
        if isinstance(widget, QCheckBox):
            widget.setChecked(value)
        elif isinstance(widget, QComboBox):
            widget.setCurrentText(value)
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value) if value is not None else '')
        elif isinstance(widget, QTextEdit):
            widget.setPlainText(str(value) if value is not None else '')
        elif isinstance(widget, QWidget) and widget.layout():
            # This is for the model_path widget
            line_edit = widget.layout().itemAt(0).widget()
            if isinstance(line_edit, QLineEdit):
                line_edit.setText(str(value) if value is not None else '')

    def get_widget_value_typed(self, widget, value_type):
        """Get the value of the widget with proper typing."""
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        elif isinstance(widget, QComboBox):
            return widget.currentText() or None
        elif isinstance(widget, QLineEdit):
            text = widget.text()
            if value_type == 'int':
                return int(text) if text else None
            elif value_type == 'float':
                return float(text) if text else None
            else:
                return text or None
        elif isinstance(widget, QTextEdit):
            text = widget.toPlainText()
            return text or None
        elif isinstance(widget, QWidget) and widget.layout():
            # This is for the model_path widget
            line_edit = widget.layout().itemAt(0).widget()
            if isinstance(line_edit, QLineEdit):
                return line_edit.text() or None
        return None

    def toggle_api_local_options(self, use_api):
        """Toggle visibility of API and local options."""
        self.iterate_settings(lambda w, c, s, k, m: self.toggle_widget_visibility(w, c, s, k, use_api))

    def toggle_widget_visibility(self, widget, category, sub_category, key, use_api):
        if sub_category in ['api', 'local']:
            widget.setVisible(use_api if sub_category == 'api' else not use_api)
            
            # Also toggle visibility of the corresponding label and help button
            label = self.findChild(QLabel, f"{category}_{sub_category}_{key}_label")
            help_button = self.findChild(QToolButton, f"{category}_{sub_category}_{key}_help")
            
            if label:
                label.setVisible(use_api if sub_category == 'api' else not use_api)
            if help_button:
                help_button.setVisible(use_api if sub_category == 'api' else not use_api)


    def iterate_settings(self, func):
        """Iterate over all settings and apply a function to each."""
        for category, settings in self.schema.items():
            for sub_category, sub_settings in settings.items():
                if isinstance(sub_settings, dict) and 'value' in sub_settings:
                    widget = self.findChild(QWidget, f"{category}_{sub_category}_input")
                    if widget:
                        func(widget, category, None, sub_category, sub_settings)
                else:
                    for key, meta in sub_settings.items():
                        widget = self.findChild(QWidget, f"{category}_{sub_category}_{key}_input")
                        if widget:
                            func(widget, category, sub_category, key, meta)

    def closeEvent(self, event):
        """Confirm before closing the settings window without saving."""
        reply = QMessageBox.question(
            self,
            'Close without saving?',
            'Are you sure you want to close without saving?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            ConfigManager.reload_config()  # Revert to last saved configuration
            self.update_widgets_from_config()
            self.settings_closed.emit()
            super().closeEvent(event)
        else:
            event.ignore()
