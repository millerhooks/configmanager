import collections
import configparser
import copy

import six

from .base import is_config_item, is_config_section
from .exceptions import NotFound
from .hooks import Hooks
from .items import Item
from .parsers import ConfigDeclarationParser
from .persistence import ConfigPersistenceAdapter, YamlReaderWriter, JsonReaderWriter, ConfigParserReaderWriter
from .simple_sections import SimpleSection


class Config(SimpleSection):
    """
    Represents a section consisting of items (instances of :class:`.Item`) and other sections
    (instances of :class:`.Config`).
    
    .. attribute:: Config(config_declaration=None, **kwargs)
        
        Creates a section from a declaration.

        Args:
            ``config_declaration``: can be a dictionary, a list, a simple class, a module, another :class:`.Config`
            instance, and a combination of these.

        Keyword Args:

            ``item_cls``:

            ``config_parser_factory``:

        Examples::

            config = Config([
                ('greeting', 'Hello!'),
                ('uploads', Config({
                    'enabled': True,
                    'tmp_dir': '/tmp',
                })),
                ('db', {
                    'host': 'localhost',
                    'user': 'root',
                    'password': 'secret',
                    'name': 'test',
                }),
                ('api', Config([
                    'host',
                    'port',
                    'default_user',
                    ('enabled', Item(type=bool)),
                ])),
            ])
    
    .. attribute:: <config>[<name_or_path>]
    
        Access item by its name, section by its alias, or either by its path.

        Args:
            ``name`` (str): name of an item or alias of a section

        Args:
            ``path`` (tuple): path of an item or a section
        
        Returns:
            :class:`.Item` or :class:`.Config`

        Examples::

            >>> config['greeting']
            <Item greeting 'Hello!'>

            >>> config['uploads']
            <Config uploads at 4436269600>

            >>> config['uploads', 'enabled'].value
            True
    
    .. attribute:: <config>.<name>
    
        Access an item by its name or a section by its alias.

        For names and aliases that break Python grammar rules, use ``config[name]`` notation instead.
        
        Returns:
            :class:`.Item` or :class:`.Config`

    .. attribute:: <name_or_path> in <Config>

        Returns ``True`` if an item or section with the specified name or path is to be found in this section.

    .. attribute:: len(<Config>)

        Returns the number of items and sections in this section (does not include sections and items in
        sub-sections).

    .. attribute:: __iter__()

        Returns an iterator over all item names and section aliases in this section.

    """

    cm__item_cls = Item
    cm__configparser_factory = configparser.ConfigParser

    def __init__(self, config_declaration=None, item_cls=None, configparser_factory=None):
        super(Config, self).__init__()
        self._cm__configparser_adapter = None
        self._cm__json_adapter = None
        self._cm__yaml_adapter = None
        self._cm__click_extension = None

        self.__dict__['hooks'] = Hooks(config=self)

        if item_cls:
            self.cm__item_cls = item_cls
        if configparser_factory:
            self.cm__configparser_factory = configparser_factory

        self._cm__process_config_declaration = ConfigDeclarationParser(section=self)

        if config_declaration:
            self._cm__process_config_declaration(config_declaration)

    def __repr__(self):
        return '<{cls} {alias} at {id}>'.format(cls=self.__class__.__name__, alias=self.alias, id=id(self))

    def _resolve_config_key(self, key):
        if isinstance(key, six.string_types):
            if key in self._cm__configs:
                return self._cm__configs[key]
            else:
                result = self.hooks.handle(Hooks.NOT_FOUND, name=key, section=self)
                if result is not None:
                    return result
                raise NotFound(key, section=self)

        if isinstance(key, (tuple, list)) and len(key) > 0:
            if len(key) == 1:
                return self._resolve_config_key(key[0])
            else:
                return self._resolve_config_key(key[0])[key[1:]]
        else:
            raise TypeError('Expected either a string or a tuple as key, got {!r}'.format(key))

    def iter_items(self, recursive=False, path=None, key='path', separator='.'):
        """

        Args:
            recursive: if ``True``, recurse into sub-sections.

            path (tuple or string): optional path to limit iteration over.

            key: ``path`` (default), ``str_path``, ``name``, or ``None``.

            separator (string): used both to interpret ``path=`` kwarg when it is a string,
                and to generate ``str_path`` as the returned key.

        Returns:
            iterator: iterator over ``(key, item)`` pairs of all items
                in this section (and sub-sections if ``recursive=True``).

        """
        for x in self.iter_all(recursive=recursive, path=path, key=key, separator=separator):
            if key is None:
                if x.is_item:
                    yield x
            elif x[1].is_item:
                yield x

    def iter_sections(self, recursive=False, path=None, key='path', separator='.'):
        """
        Args:
            recursive: if ``True``, recurse into sub-sections.

            path (tuple or string): optional path to limit iteration over.

            key: ``path`` (default), ``str_path``, ``alias``, or ``None``.

            separator (string): used both to interpret ``path=`` kwarg when it is a string,
                and to generate ``str_path`` as the returned key.

        Returns:
            iterator: iterator over ``(key, section)`` pairs of all sections
                in this section (and sub-sections if ``recursive=True``).

        """
        for x in self.iter_all(recursive=recursive, path=path, key=key, separator=separator):
            if key is None:
                if x.is_section:
                    yield x
            elif x[1].is_section:
                yield x

    def iter_paths(self, recursive=False, path=None, key='path', separator='.'):
        """

        Args:
            recursive: if ``True``, recurse into sub-sections

            path (tuple or string): optional path to limit iteration over.

            key: ``path`` (default), ``str_path``, or ``name``.

            separator (string): used both to interpret ``path=`` kwarg when it is a string,
                and to generate ``str_path`` as the returned key.

        Returns:
            iterator: iterator over paths of all items and sections
            contained in this section.

        """
        assert key is not None
        for path, _ in self.iter_all(recursive=recursive, path=path, key=key, separator=separator):
            yield path

    def dump_values(self, with_defaults=True, dict_cls=dict):
        """
        Export values of all items contained in this section to a dictionary.

        Items with no values set (and no defaults set if ``with_defaults=True``) will be excluded.
        
        Returns:
            dict: A dictionary of key-value pairs, where for sections values are dictionaries
            of their contents.
        
        """
        values = dict_cls()
        for item_name, item in self._cm__configs.items():
            if is_config_section(item):
                section_values = item.dump_values(with_defaults=with_defaults, dict_cls=dict_cls)
                if section_values:
                    values[item_name] = section_values
            else:
                if item.has_value:
                    if with_defaults or not item.is_default:
                        values[item.name] = item.value
        return values

    def load_values(self, dictionary, as_defaults=False):
        """
        Import config values from a dictionary.
        
        When ``as_defaults`` is set to ``True``, the values
        imported will be set as defaults. This can be used to
        declare the sections and items of configuration.
        Values of sections and items in ``dictionary`` can be
        dictionaries as well as instances of :class:`.Item` and
        :class:`.Config`.
        
        Args:
            dictionary: 
            as_defaults: if ``True``, the imported values will be set as defaults.
        """
        for name, value in dictionary.items():
            if name not in self:
                if as_defaults:
                    if isinstance(value, dict):
                        self[name] = self.create_section()
                        self[name].load_values(value, as_defaults=as_defaults)
                    else:
                        self[name] = self.create_item(name, default=value)
                else:
                    # Skip unknown names if not interpreting dictionary as defaults
                    continue
            elif is_config_item(self[name]):
                if as_defaults:
                    self[name].default = value
                else:
                    self[name].value = value
            else:
                self[name].load_values(value, as_defaults=as_defaults)

    def reset(self):
        """
        Recursively resets values of all items contained in this section
        and its subsections to their default values.
        """
        for _, item in self.iter_items(recursive=True):
            item.reset()

    @property
    def is_default(self):
        """
        ``True`` if values of all config items in this section and its subsections
        have their values equal to defaults or have no value set.
        """
        for _, item in self.iter_items(recursive=True):
            if not item.is_default:
                return False
        return True

    @property
    def section(self):
        """
        Returns:
            (:class:`.Config`): section to which this section belongs or ``None`` if this
            hasn't been added to any section.
        """
        return self._cm__section

    @property
    def alias(self):
        """
        Returns alias with which this section was added to another or ``None`` if it hasn't been added
        to any.
        
        Returns:
            (str)
        """
        return self._cm__section_alias

    @property
    def configparser(self):
        """
        Adapter to dump/load INI format strings and files using standard library's
        ``ConfigParser`` (or the backported configparser module in Python 2).
        
        Returns:
            :class:`.ConfigPersistenceAdapter`
        """
        if self._cm__configparser_adapter is None:
            self._cm__configparser_adapter = ConfigPersistenceAdapter(
                config=self,
                reader_writer=ConfigParserReaderWriter(
                    config_parser_factory=self.cm__configparser_factory,
                ),
            )
        return self._cm__configparser_adapter

    @property
    def json(self):
        """
        Adapter to dump/load JSON format strings and files.
        
        Returns:
            :class:`.ConfigPersistenceAdapter`
        """
        if self._cm__json_adapter is None:
            self._cm__json_adapter = ConfigPersistenceAdapter(
                config=self,
                reader_writer=JsonReaderWriter(),
            )
        return self._cm__json_adapter

    @property
    def yaml(self):
        """
        Adapter to dump/load YAML format strings and files.
        
        Returns:
            :class:`.ConfigPersistenceAdapter`
        """
        if self._cm__yaml_adapter is None:
            self._cm__yaml_adapter = ConfigPersistenceAdapter(
                config=self,
                reader_writer=YamlReaderWriter(),
            )
        return self._cm__yaml_adapter

    @property
    def click(self):
        if self._cm__click_extension is None:
            from .click_ext import ClickExtension
            self._cm__click_extension = ClickExtension(
                config=self
            )
        return self._cm__click_extension

    def create_item(self, *args, **kwargs):
        """
        Internal factory method used to create an instance of configuration item.
        Should only be used to extend configmanager's functionality.
        """
        return self.cm__item_cls(*args, **kwargs)

    def create_section(self, *args, **kwargs):
        """
        Internal factory method used to create an instance of configuration section.
        Should only be used to extend configmanager's functionality.
        """
        return self.__class__(*args, **kwargs)

    def add_item(self, alias, item):
        """
        Add a config item to this section.
        """
        super(Config, self).add_item(alias, item)

        # Since we are actually deep-copying the supplied item,
        # the actual item to pass to callbacks needs to be fetched from the section.
        item = self[alias]

        self.hooks.handle(Hooks.ITEM_ADDED_TO_SECTION, alias=alias, section=self, subject=item)

    def add_section(self, alias, section):
        """
        Add a sub-section to this section.
        """
        super(Config, self).add_section(alias, section)
        self.hooks.handle(Hooks.SECTION_ADDED_TO_SECTION, alias=alias, section=self, subject=section)
