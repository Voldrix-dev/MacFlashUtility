#!/usr/bin/env python3
"""
i18n module for MacFlashUtility.
Provides translation support using Qt's QTranslator.
"""

import os
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QTranslator, QLocale, QSettings


class Translator:
    """Manages application translations."""
    
    _instance = None
    _translator = None
    _current_locale = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self._translator = QTranslator()
        self._app_translator = QTranslator()
        self._current_locale = "ru"  # default
        
    def load(self, locale: str = None):
        """Load translation for given locale (e.g., 'ru', 'en')."""
        if locale is None:
            # Try to get from settings, fallback to system locale
            settings = QSettings("MacFlashUtility", "MacFlashUtility")
            locale = settings.value("locale", "")
            if not locale:
                system_locale = QLocale.system().name().split("_")[0].lower()
                locale = system_locale if system_locale in ("ru", "en") else "ru"
        
        locale = locale.lower()
        if locale not in ("ru", "en"):
            locale = "ru"
            
        self._current_locale = locale
        
        # Get translations directory
        translations_dir = Path(__file__).parent / "translations"
        
        # Load Qt base translations
        qt_base = f"qtbase_{locale}"
        if self._translator.load(qt_base, str(translations_dir)):
            QCoreApplication.installTranslator(self._translator)
            
        # Load app translations
        app_ts = f"macflash_{locale}"
        if self._app_translator.load(app_ts, str(translations_dir)):
            QCoreApplication.installTranslator(self._app_translator)
            
        # Save to settings
        settings = QSettings("MacFlashUtility", "MacFlashUtility")
        settings.setValue("locale", locale)
        
        return locale
    
    def get_locale(self) -> str:
        return self._current_locale
    
    def set_locale(self, locale: str):
        """Change locale at runtime."""
        self.load(locale)
        
    def tr(self, source_text: str, disambiguation: str = None, n: int = -1) -> str:
        """Translate text using QCoreApplication."""
        return QCoreApplication.translate("MacFlashUtility", source_text, disambiguation, n)


# Global instance
_translator = Translator()


def tr(source_text: str, disambiguation: str = None, n: int = -1) -> str:
    """Convenience function for translation."""
    return _translator.tr(source_text, disambiguation, n)


def init_translations(locale: str = None) -> str:
    """Initialize translations at app startup."""
    return _translator.load(locale)


def get_current_locale() -> str:
    return _translator.get_locale()


def set_locale(locale: str):
    _translator.set_locale(locale)