################################################################################
# do_import.py
#
# This is the actual top-level import process.
################################################################################

import csv
import os
import traceback

import pdsfile

import opus_support

from config_data import *
import do_cart
import do_django
import impglobals
import import_util

from populate_obs_general import *
from populate_obs_pds import *

from populate_obs_mission_cassini import *
from populate_obs_instrument_COCIRS import *
from populate_obs_instrument_COISS import *
from populate_obs_instrument_CORSS import *
from populate_obs_instrument_COUVIS import *
from populate_obs_instrument_COVIMS import *

from populate_obs_mission_galileo import *
from populate_obs_instrument_GOSSI import *

from populate_obs_mission_hubble import *

from populate_obs_mission_new_horizons import *
from populate_obs_instrument_NHLORRI import *
from populate_obs_instrument_NHMVIC import *

from populate_obs_mission_voyager import *
from populate_obs_instrument_VGISS import *

from populate_obs_mission_earthbased_occ import *
from populate_obs_instrument_EB_occ import *

from populate_obs_surface_geo import *


################################################################################
# TABLE PREPARATION
################################################################################

def delete_all_obs_mult_tables(namespace):
    """Delete ALL import or permanent (as specified by namespace)
    obs_ and mult_ tables."""

    table_names = impglobals.DATABASE.table_names(namespace,
                                                  prefix=['obs_', 'mult_',
                                                          'cart'])
    table_names = sorted(table_names)
    # This has to happen in four phases to handle foreign key contraints:
    # 1. All obs_ tables except obs_general
    for table_name in table_names:
        if (table_name.startswith('obs_') and
            table_name != 'obs_general'):
            impglobals.DATABASE.drop_table(namespace, table_name)

    # 2. cart
    if 'cart' in table_names:
        impglobals.DATABASE.drop_table(namespace, 'cart')

    # 3. obs_general
    if 'obs_general' in table_names:
        impglobals.DATABASE.drop_table(namespace, 'obs_general')

    # 4. All mult_YYY tables
    for table_name in table_names:
        if table_name.startswith('mult_'):
            impglobals.DATABASE.drop_table(namespace, table_name)


def delete_volume_from_obs_tables(volume_id, namespace):
    "Delete a single volume from all import or permanent obs tables."

    impglobals.LOGGER.log('info',
        f'Deleting volume "{volume_id}" from {namespace} tables')

    table_names = impglobals.DATABASE.table_names(namespace,
                                                  prefix=['obs_', 'mult_'])
    table_names = sorted(table_names)
    q = impglobals.DATABASE.quote_identifier
    where = f'{q("volume_id")}="{volume_id}"'

    # This has to happen in two phases to handle foreign key contraints:
    # 1. All tables except obs_general
    for table_name in table_names:
        if (table_name.startswith('obs_') and
            table_name != 'obs_general'):
            impglobals.DATABASE.delete_rows(namespace, table_name, where)

    # 2. obs_general
    if 'obs_general' in table_names:
        impglobals.DATABASE.delete_rows(namespace, 'obs_general', where)


def find_duplicate_opus_ids():
    """Find opus_ids that exist in both import and permanent tables.
       This can only happen in real life if the same opus_id appears in
       two different volumes, since normally we delete and entire volume
       before getting here. Sadly this really happens with GOSSI."""

    if (not impglobals.DATABASE.table_exists('import', 'obs_general') or
        not impglobals.DATABASE.table_exists('perm', 'obs_general')):
        return []

    imp_obs_general_table_name = impglobals.DATABASE.convert_raw_to_namespace(
            'import', 'obs_general')
    perm_obs_general_table_name = impglobals.DATABASE.convert_raw_to_namespace(
            'perm', 'obs_general')

    q = impglobals.DATABASE.quote_identifier
    cmd = f"""
    og.{q('opus_id')} FROM
    {q(perm_obs_general_table_name)} og,
    {q(imp_obs_general_table_name)} iog WHERE
    og.{q('opus_id')} = iog.{q('opus_id')}"""
    res = impglobals.DATABASE.general_select(cmd)
    return [x[0] for x in res]


def delete_opus_id_from_obs_tables(opus_id, namespace):
    "Delete a single opus_id from all import or permanent obs tables."

    impglobals.LOGGER.log('info',
        f'Deleting opus_id "{opus_id}" from {namespace} tables')

    table_names = impglobals.DATABASE.table_names(namespace,
                                                  prefix=['obs_', 'mult_'])
    table_names = sorted(table_names)
    q = impglobals.DATABASE.quote_identifier
    where = f'{q("opus_id")}="{opus_id}"'

    # This has to happen in two phases to handle foreign key contraints:
    # 1. All tables except obs_general
    for table_name in table_names:
        if (table_name.startswith('obs_') and
            table_name != 'obs_general'):
            impglobals.DATABASE.delete_rows(namespace, table_name, where)

    # 2. obs_general
    if 'obs_general' in table_names:
        impglobals.DATABASE.delete_rows(namespace, 'obs_general', where)


def delete_duplicate_opus_id_from_perm_tables():
    opus_ids = find_duplicate_opus_ids()
    for opus_id in opus_ids:
        delete_opus_id_from_obs_tables(opus_id, 'perm')


def create_tables_for_import(volume_id, namespace):
    """Create the import or permanent obs_ tables and all the mult tables they
       reference. This does NOT create the target-specific obs_surface_geometry
       tables because we don't yet know what target names we have."""

    volume_id_prefix = volume_id[:volume_id.find('_')]
    instrument_name = VOLUME_ID_PREFIX_TO_INSTRUMENT_NAME[volume_id_prefix]
    if instrument_name is None:
        instrument_name = 'EB'
    mission_abbrev = VOLUME_ID_PREFIX_TO_MISSION_ABBREV[volume_id_prefix]
    mission_name = MISSION_ABBREV_TO_MISSION_TABLE_SFX[mission_abbrev]

    mult_table_schema = import_util.read_schema_for_table('mult_template')

    # This is an awful hack because this one mult table has an extra field
    # in it. Yuck! XXX
    mult_target_name_table_schema = (
                import_util.read_schema_for_table(
                        'mult_target_name_template'))

    table_schemas = {}
    table_names_in_order = []
    for table_name in TABLES_TO_POPULATE:
        table_name = table_name.replace('<INST>', instrument_name.lower())
        table_name = table_name.replace('<MISSION>', mission_name.lower())

        if table_name.startswith('obs_surface_geometry__'):
            # Note that we aren't replacing <TARGET> here because we don't know
            # the target name! We're only using this schema to get field names,
            # data source, source order, etc. The real use of the schema will be
            # later when we finally create and insert into the correct table for
            # each target.
            table_schema = import_util.read_schema_for_table(
                                                'obs_surface_geometry_target')
        else:
            table_schema = import_util.read_schema_for_table(table_name)
        if table_schema is None:
            continue

        table_schemas[table_name] = table_schema
        table_names_in_order.append(table_name)

        if table_name.startswith('obs_surface_geometry__'):
            # Skip surface geo tables until they are needed
            continue

        # Create the referenced mult_ tables
        for table_column in table_schema:
            if table_column.get('put_mults_here', False):
                continue
            field_name = table_column['field_name']
            pi_form_type = table_column.get('pi_form_type', None)
            if pi_form_type is not None and pi_form_type.find(':') != -1:
                pi_form_type = pi_form_type[:pi_form_type.find(':')]
            if pi_form_type in GROUP_FORM_TYPES:
                mult_name = import_util.table_name_mult(table_name, field_name)
                if mult_name in MULT_TABLES_WITH_TARGET_GROUPING:
                    schema = mult_target_name_table_schema
                else:
                    schema = mult_table_schema
                if (impglobals.DATABASE.create_table(namespace, mult_name,
                                                    schema) and
                    namespace == 'import'):
                    _CREATED_IMP_MULT_TABLES.add(mult_name)

        impglobals.DATABASE.create_table(namespace, table_name,
                                         table_schema)

    return table_schemas, table_names_in_order


def copy_volume_from_import_to_permanent(volume_id):
    """Copy a single volume from all import obs tables to the corresponding
       permanent tables. Create the permanent obs tables if they don't already
       exist. As usual, we have to treat the obs_surface_geometry__<T> tables
       specially."""

    impglobals.LOGGER.log('info',
        f'Copying volume "{volume_id}" from import to permanent')

    q = impglobals.DATABASE.quote_identifier

    table_schemas, table_names_in_order = create_tables_for_import(
                                                    volume_id,
                                                    namespace='perm')
    for table_name in table_names_in_order:
        if table_name.startswith('obs_surface_geometry__'):
            continue
        impglobals.LOGGER.log('debug', f'Copying table "{table_name}"')
        where = f'{q("volume_id")}="{volume_id}"'
        impglobals.DATABASE.copy_rows_between_namespaces('import', 'perm',
                                                         table_name,
                                                         where=where)

    # For obs_surface_geometry__<T> we don't even know the target names at
    # this point, so we actually have to look at the table names in the database
    # to see what to copy! Also the tables may not have been created yet.

    surface_geo_table_names = impglobals.DATABASE.table_names(
                                            'import',
                                            prefix='obs_surface_geometry__')
    for table_name in sorted(surface_geo_table_names):
        target_name = table_name.replace('obs_surface_geometry__', '')
        if not impglobals.DATABASE.table_exists('perm', table_name):
            table_schema = import_util.read_schema_for_table(
                                            'obs_surface_geometry_target',
                                            replace=[
               ('<TARGET>', import_util.table_name_for_sfc_target(target_name)),
               ('<SLUGTARGET>', import_util.slug_name_for_sfc_target(target_name))])
            impglobals.DATABASE.create_table('perm', table_name, table_schema)
        impglobals.LOGGER.log('debug', f'Copying table "{table_name}"')
        where = f'{q("volume_id")}="{volume_id}"'
        impglobals.DATABASE.copy_rows_between_namespaces('import', 'perm',
                                                         table_name,
                                                         where=where)

def read_existing_import_opus_id():
    """Return a list of all opus_id used in the import tables. Used to check
       for duplicates during import."""
    impglobals.LOGGER.log('debug', 'Collecting previous import opus_ids')

    imp_obs_general_table_name = impglobals.DATABASE.convert_raw_to_namespace(
                                                        'import', 'obs_general')
    if (not impglobals.DATABASE.table_exists('import', 'obs_general') and
        impglobals.ARGUMENTS.read_only):
        # It's OK if we don't have this table in read-only mode, because perhaps
        # nobody ever created it before.
        return []

    q = impglobals.DATABASE.quote_identifier
    rows = impglobals.DATABASE.general_select(
        f'{q("opus_id")} FROM {q(imp_obs_general_table_name)}')

    return [x[0] for x in rows]


def analyze_all_tables(namespace):
    """Analyze ALL import or permanent (as specified by namespace)
    obs_ and mult_ tables."""

    table_names = impglobals.DATABASE.table_names(namespace,
                                                  prefix=['obs_', 'mult_'])
    table_names = sorted(table_names)
    for table_name in table_names:
        impglobals.DATABASE.analyze_table(namespace, table_name)


################################################################################
# MULT TABLE HANDLING
################################################################################

# We cache the contents of mult_ tables we've touched so we don't have to keep
# reading them from the database.
_MULT_TABLE_CACHE = None
_CREATED_IMP_MULT_TABLES = None
_MODIFIED_MULT_TABLES = None

def _mult_table_column_names(table_name):
    """Return a list of the columns found in a mult tables. This isn't
       constant because various *_target_name tables have an extra
       column used for target name grouping."""

    column_list = ['id', 'value', 'label', 'disp_order', 'display']
    if table_name in MULT_TABLES_WITH_TARGET_GROUPING:
        column_list.append('grouping')
    return column_list

def _convert_sql_response_to_mult_table(mult_table_name, rows):
    """Given a set of rows from an SQL query of a mult table, convert it into
       our internal dictionary representation."""

    mult_rows = []
    for row in rows:
        id_num, value, label, disp_order, display = row[:5]
        row_dict = {
            'id': id_num,
            'value': value,
            'label': str(label),
            'disp_order': disp_order,
            'display': display
        }
        if mult_table_name in MULT_TABLES_WITH_TARGET_GROUPING:
            row_dict['grouping'] = row[5]
        mult_rows.append(row_dict)
    return mult_rows

def read_or_create_mult_table(mult_table_name, table_column):
    """Given a mult table name, either read the table from the database or
       return the cached version if we previously read it."""

    if mult_table_name in _MULT_TABLE_CACHE:
        return _MULT_TABLE_CACHE[mult_table_name]

    use_mult_table_name = None

    if 'mult_options' in table_column:
        if not impglobals.ARGUMENTS.import_suppress_mult_messages:
            impglobals.LOGGER.log('debug',
                      f'Using preprogrammed mult table "{mult_table_name}"')
        mult_rows = _convert_sql_response_to_mult_table(
                            mult_table_name,
                            table_column['mult_options'])
        _MULT_TABLE_CACHE[mult_table_name] = mult_rows
        _MODIFIED_MULT_TABLES[mult_table_name] = table_column
        return mult_rows

    # If there is already an import version of the table, it means this is a
    # second run of the import pipeline without copying over to the new
    # database, so read the contents of the import version. But it's also
    # possible we just created the mult table during the initalization phase
    # and it's empty. In that case ignore the import one.
    # Otherwise, if there is already a non-import version, read that one.
    # And if there's no table to be found anyway, create a new one.
    use_namespace = None

    if (mult_table_name not in _CREATED_IMP_MULT_TABLES and
        impglobals.DATABASE.table_exists('import', mult_table_name)):
        # Previous import table available
        use_namespace = 'import'

    elif impglobals.DATABASE.table_exists('perm', mult_table_name):
        use_namespace = 'perm'
        # If we just created an import version but are reading the permanent
        # version, we have to write out the import version before doing
        # anything else too
        if mult_table_name in _CREATED_IMP_MULT_TABLES:
            _MODIFIED_MULT_TABLES[mult_table_name] = table_column

    if use_namespace is not None:
        ns_mult_table_name = (
            impglobals.DATABASE.convert_raw_to_namespace(use_namespace,
                                                         mult_table_name))
        if not impglobals.ARGUMENTS.import_suppress_mult_messages:
            impglobals.LOGGER.log('debug',
                      f'Reading from mult table "{ns_mult_table_name}"')
        rows = impglobals.DATABASE.read_rows(
                    use_namespace,
                    mult_table_name,
                    _mult_table_column_names(mult_table_name))
        mult_rows = _convert_sql_response_to_mult_table(mult_table_name,
                                                        rows)
        _MULT_TABLE_CACHE[mult_table_name] = mult_rows
        return mult_rows

    rows = []
    _MULT_TABLE_CACHE[mult_table_name] = rows
    return rows


def update_mult_table(table_name, field_name, table_column, val, label):
    """Update a single value in the cached version of a mult table."""

    mult_table_name = import_util.table_name_mult(table_name, field_name)
    mult_table = read_or_create_mult_table(mult_table_name, table_column)
    if val is not None:
        val = str(val)
    for entry in mult_table:
        if entry['value'] == val:
            # The value is already in the mult table, so we're done here
            return entry['id']

    if 'mult_options' in table_column:
        import_util.log_nonrepeating_error(
            f'Attempting to add value "{val}" to preprogrammed mult table '+
            f'"{mult_table_name}"')
        return 0

    if len(mult_table) == 0:
        next_id = 0
        next_disp_order = 10
    else:
        next_id = max([x['id'] for x in mult_table])+1
        next_disp_order = max([x['disp_order'] for x in mult_table])+10
    if label is None:
        label = 'N/A'
    new_entry = {
        'id': next_id,
        'value': val,
        'label': str(label),
        'disp_order': 0,
        'display': 'Y' # if label is not None else 'N'
    }
    if mult_table_name in MULT_TABLES_WITH_TARGET_GROUPING:
        if val not in TARGET_NAME_INFO:
            planet_id = 'OTHER'
        else:
            planet_id = TARGET_NAME_INFO[val][0]
            if planet_id is None:
                planet_id = 'OTHER'
        new_entry['grouping'] = planet_id
    mult_table.append(new_entry)

    _MODIFIED_MULT_TABLES[mult_table_name] = table_column
    if not impglobals.ARGUMENTS.import_suppress_mult_messages:
        impglobals.LOGGER.log('info',
            f'Added new value "{val}" ("{label}") to mult table '+
            f'"{mult_table_name}"')

    return next_id


def dump_import_mult_tables():
    """Dump all of the cached import mult tables into the database."""

    for mult_table_name in sorted(_MODIFIED_MULT_TABLES):
        table_column = _MODIFIED_MULT_TABLES[mult_table_name]
        rows = _MULT_TABLE_CACHE[mult_table_name]
        # Update the display_order
        form_type = table_column['pi_form_type']
        range_func = None
        if form_type is not None and form_type.startswith('GROUP:'):
            range_func_name = form_type.replace('GROUP:', '')
            if range_func_name in opus_support.RANGE_FUNCTIONS:
                range_func = opus_support.RANGE_FUNCTIONS[range_func_name][1]
        if 'mult_options' not in table_column:
            all_numeric = True
            for row in rows:
                if (row['label'] is None or
                    str(row['label']).upper() == 'NONE' or
                    str(row['label']).upper() == 'NULL'):
                    continue
                try:
                    float(row['label'])
                except ValueError:
                    all_numeric = False
                    break
            for row in rows:
                # Null always comes last. N/A always comes before that.
                # None always comes before that.
                # Yes comes before No. On comes before Off.
                if row['label'] in [None, 'NULL', 'Null']:
                    if range_func is not None:
                        row['sort_label'] = 1e38
                    else:
                        row['sort_label'] = 'ZZZ' + str(row['label'])
                elif row['label'] == 'N/A':
                    if range_func is not None:
                        row['sort_label'] = 1e37
                    else:
                        row['sort_label'] = 'ZZY' + str(row['label'])
                elif row['label'] in ['NONE', 'None']:
                    if range_func is not None:
                        row['sort_label'] = 1e37
                    else:
                        row['sort_label'] = 'ZZX' + str(row['label'])
                elif range_func:
                    try:
                        row['sort_label'] = range_func(str(row['value']))
                    except Exception as e:
                        label = str(row['label'])
                        import_util.log_nonrepeating_error(
    f'Unable to parse "{label}" for type "range_func_name": {e}')
                        row['sort_label'] = str(row['label'])
                elif all_numeric:
                    row['sort_label'] = ('%20.9f' % float(row['label']))
                elif row['label'] in ('Yes', 'On'):
                    row['sort_label'] = 'ZZAYes'
                elif row['label'] in ('No', 'Off'):
                    row['sort_label'] = 'ZZBNo'
                else:
                    row['sort_label'] = str(row['label'])
            rows.sort(key=lambda x: x['sort_label'])
            for row in rows:
                del row['sort_label']
            for i, row in enumerate(rows):
                row['disp_order'] = (i+1)*10
        # Insert or update all the rows
        imp_mult_table_name = impglobals.DATABASE.convert_raw_to_namespace(
                'import', mult_table_name)
        impglobals.LOGGER.log('debug',
            f'Writing mult table "{imp_mult_table_name}"')
        impglobals.DATABASE.upsert_rows('import', mult_table_name, 'id', rows)
        # If we wrote out a mult table, that means we didn't just create it
        # empty anymore, so remove it from _CREATED_IMP_MULT_TABLES.
        if mult_table_name in _CREATED_IMP_MULT_TABLES:
            _CREATED_IMP_MULT_TABLES.remove(mult_table_name)


def copy_mult_from_import_to_permanent():
    """Copy ALL mult tables from import to permament. We have to do all tables,
       not just the ones that have changed, because tables might have changed
       during previous import runs or previous volumes and we don't have a
       record of that."""

    table_names = impglobals.DATABASE.table_names('import', prefix='mult_')
    for table_name in table_names:
        imp_mult_table_name = impglobals.DATABASE.convert_raw_to_namespace(
                'import', table_name)
        impglobals.LOGGER.log('debug',
                              f'Copying mult table "{imp_mult_table_name}"')
        # Read the import mult table
        column_list = _mult_table_column_names(table_name)
        rows = impglobals.DATABASE.read_rows(
                    'import', table_name,
                    column_list)
        mult_rows = _convert_sql_response_to_mult_table(table_name, rows)
        # Write the permanent table
        impglobals.DATABASE.upsert_rows('perm', table_name, 'id', mult_rows)


################################################################################
# TOP-LEVEL IMPORT ROUTINES
################################################################################

def import_one_volume(volume_id):
    """Read the PDS data and perform all import functions for one volume.
       The results are left in the import namespace."""
    impglobals.LOGGER.open(
            f'Importing {volume_id}',
            limits={'info': impglobals.ARGUMENTS.log_info_limit,
                    'debug': impglobals.ARGUMENTS.log_debug_limit})

    # Start fresh
    global _MULT_TABLE_CACHE, _MODIFIED_MULT_TABLES
    _MULT_TABLE_CACHE = {}
    _MODIFIED_MULT_TABLES = {}
    impglobals.ANNOUNCED_IMPORT_WARNINGS = []
    impglobals.ANNOUNCED_IMPORT_ERRORS = []
    impglobals.MAX_TABLE_ID_CACHE = {}
    impglobals.CURRENT_VOLUME_ID = volume_id
    impglobals.CURRENT_INDEX_ROW_NUMBER = None

    volume_pdsfile = pdsfile.PdsFile.from_path(volume_id)
    if not volume_pdsfile.is_volume():
        impglobals.LOGGER.log('error', f'{volume_id} is not a volume!')
        impglobals.LOGGER.close()
        impglobals.CURRENT_VOLUME_ID = None
        return False


    #################################
    ### FIND RELEVANT INDEX FILES ###
    #################################

    # First see if we have a brand new label and index tab in the metadata
    # directory. If so, ignore the one in volumes because it's probably
    # broken.
    paths = volume_pdsfile.associated_logical_paths('metadata', must_exist=True)
    volume_label_path = None

    if paths:
        for path in paths:
            assoc_pdsfile = pdsfile.PdsFile.from_logical_path(path)
            basenames = assoc_pdsfile.childnames
            for basename in basenames:
                if (basename.upper().endswith('_RAW_IMAGE_INDEX.LBL') and
                    basename.find('999') == -1):
                    volume_label_path = os.path.join(assoc_pdsfile.abspath,
                                                     basename)
                    impglobals.LOGGER.log('debug',
                f'Using raw_image_index metadata version of main index: '+
                f'"{volume_label_path}"')
                    break

    if volume_label_path is None:
        if paths:
            for path in paths:
                assoc_pdsfile = pdsfile.PdsFile.from_logical_path(path)
                basenames = assoc_pdsfile.childnames
                for basename in basenames:
                    if (basename.upper().endswith('_INDEX.LBL') and
                        not basename.upper().endswith(
                                            'SUPPLEMENTAL_INDEX.LBL') and
                        basename.find('999') == -1):
                        volume_label_path = os.path.join(assoc_pdsfile.abspath,
                                                         basename)
                        impglobals.LOGGER.log('debug',
                    f'Using metadata version of main index: '+
                    f'"{volume_label_path}"')
                        break

    # Now look for non-metadata versions if we haven't found an index yet.
    if (volume_label_path is None and
        not impglobals.ARGUMENTS.import_force_metadata_index):
        # COCIRS - give preference to OBSINDEX over INDEX
        volume_label_path = os.path.join(volume_pdsfile.abspath,
                                         'INDEX', 'OBSINDEX.LBL')
        if not os.path.exists(volume_label_path):
            # Voyager
            volume_label_path = os.path.join(volume_pdsfile.abspath,
                                             'INDEX', 'IMGINDEX.LBL')
        if not os.path.exists(volume_label_path):
            # If not COCIRS or Voyager, loop through the INDEX directory
            # to find all the index files.
            found_label_file = False
            ret = True
            for index_dir in ('INDEX', 'index'):
                for root, dirs, files in os.walk(os.path.join(
                                                    volume_pdsfile.abspath,
                                                    index_dir)):
                    for filename in sorted(files):
                        if filename.lower().endswith('index.lbl'):
                            found_label_file = True
                            volume_label_path = os.path.join(root, filename)
                            ret = ret and import_one_index(volume_id,
                                                           volume_pdsfile,
                                                           paths,
                                                           volume_label_path)

            if found_label_file:
                impglobals.LOGGER.close()
                impglobals.CURRENT_VOLUME_ID = None
                impglobals.CURRENT_INDEX_ROW_NUMBER = None
                return ret

        if not os.path.exists(volume_label_path):
            volume_label_path = os.path.join(volume_pdsfile.abspath,
                                             'index', 'index.lbl')
        if not os.path.exists(volume_label_path):
            volume_label_path = os.path.join(volume_pdsfile.abspath,
                                             'INDEX', 'INDEX.LBL')
        if not os.path.exists(volume_label_path):
            volume_label_path = os.path.join(volume_pdsfile.abspath,
                                             'INDEX', 'index.lbl')
    if volume_label_path is None:
        impglobals.LOGGER.log('error',
            f'No appropriate label file found: "{volume_id}"')
        impglobals.LOGGER.close()
        impglobals.CURRENT_VOLUME_ID = None
        return False

    if not os.path.exists(volume_label_path):
        impglobals.LOGGER.log('error',
            f'Label file does not exist: "{volume_label_path}"')
        impglobals.LOGGER.close()
        impglobals.CURRENT_VOLUME_ID = None
        return False

    ret = import_one_index(volume_id, volume_pdsfile, paths, volume_label_path)

    impglobals.LOGGER.close()
    impglobals.CURRENT_VOLUME_ID = None
    impglobals.CURRENT_INDEX_ROW_NUMBER = None

    return ret

def import_one_index(volume_id, volume_pdsfile, paths, volume_label_path):
    volume_id_prefix = volume_id[:volume_id.find('_')]
    instrument_name = VOLUME_ID_PREFIX_TO_INSTRUMENT_NAME[volume_id_prefix]
    mission_abbrev = VOLUME_ID_PREFIX_TO_MISSION_ABBREV[volume_id_prefix]

    obs_rows, obs_label_dict = import_util.safe_pdstable_read(volume_label_path)
    if not obs_rows:
        impglobals.LOGGER.log('error', f'Read failed: "{volume_label_path}"')
        return False

    impglobals.LOGGER.log('info',
               f'OBSERVATIONS: {len(obs_rows)} in {volume_label_path}')

    # This is used to make all Earth-based observations look like they were
    # done by a single instrument to make writing the populate_* functions
    # easier.
    func_instrument_name = instrument_name
    if instrument_name is None:
        # There could be multiple instruments in a single volume, in which case
        # we need to look in the index labelself.
        # We assume this only happens for Earth-based instruments.
        instrument_name = (obs_label_dict['INSTRUMENT_HOST_ID']
                           +obs_label_dict['INSTRUMENT_ID'])
        func_instrument_name = 'EB'

    metadata = {}
    metadata['index'] = obs_rows
    metadata['index_label'] = obs_label_dict


    ######################################
    ### FIND ASSOCIATED METADATA FILES ###
    ######################################

    # Look for all the "associated" metadata files and read them in
    # Metadata filenames include:
    #   <vol>_ring_summary.lbl
    #   <vol>_moon_summary.lbl
    #   <vol>_<planet>_summary.lbl
    #   <vol>_supplemental_index.lbl

    if paths:
        for path in paths:
            assoc_pdsfile = pdsfile.PdsFile.from_logical_path(path)
            basenames = assoc_pdsfile.childnames
            for basename in basenames:
                if basename.find('999') != -1:
                    # These are cumulative geo files
                    continue
                if (not basename.upper().endswith('SUMMARY.LBL') and
                    not basename.upper().endswith('SUPPLEMENTAL_INDEX.LBL') and
                    not basename.upper().endswith('INVENTORY.LBL')):
                    continue
                assoc_label_path = os.path.join(assoc_pdsfile.abspath,
                                                basename)
                if not basename.upper().endswith('INVENTORY.LBL'):
                    (assoc_rows,
                     assoc_label_dict) = import_util.safe_pdstable_read(
                                                            assoc_label_path)
                else:
                    # The pdstable.py module can't read non-fixed-length records
                    # so we fake it up ourselves here.
                    table_filename = (assoc_label_path.replace('.LBL', '.TAB')
                                      .replace('.lbl', '.tab'))
                    assoc_rows = []
                    assoc_label_dict = {} # Not used
                    with open(table_filename, 'r') as table_file:
                        csvreader = csv.reader(table_file)
                        for row in csvreader:
                            (csv_volume, csv_filespec, csv_ringobsid,
                             *csv_targets) = row
                            row_dict = {'VOLUME_ID': csv_volume.strip(),
                                        'FILE_SPECIFICATION_NAME':
                                                     csv_filespec.strip(),
                                        'RING_OBSERVATION_ID':
                                                     csv_ringobsid.strip(),
                                        'TARGET_LIST': csv_targets}
                            assoc_rows.append(row_dict)
                if not assoc_rows:
                    # No need to report an error here because safe_pdstable_read
                    # will have already done so
                    # impglobals.LOGGER.log('error',
                    #                       f'Read failed: "{assoc_label_path}"')
                    if impglobals.ARGUMENTS.import_ignore_errors:
                        impglobals.IMPORT_HAS_BAD_DATA = True
                        continue
                    return False
                if 'RING_SUMMARY' in basename.upper():
                    assoc_type = 'ring_geo'
                elif 'SUPPLEMENTAL_INDEX' in basename.upper():
                    assoc_type = 'supp_index'
                elif 'INVENTORY' in basename.upper():
                    assoc_type = 'inventory'
                else:
                    assoc_type = 'body_surface_geo'
                impglobals.LOGGER.log('info',
        f'{assoc_type.upper()}: {len(assoc_rows)} in {assoc_label_path}')
                assoc_dict = metadata.get(assoc_type, {})
                if (assoc_type == 'ring_geo' or
                    assoc_type == 'body_surface_geo' or
                    assoc_type == 'inventory'):
                    for row in assoc_rows:
                        key = None
                        geo_vol = row.get('VOLUME_ID', None)
                        if geo_vol != volume_id:
                            import_util.log_nonrepeating_error(
        f'{assoc_label_path} VOLUME_ID "{geo_vol}" does not match current '+
        f'import volume "{volume_id}"')
                            geo_vol = volume_id # XXX FOR NOW BECAUSE OF NH
                        if geo_vol is None:
                            import_util.log_nonrepeating_error(
                               f'{assoc_label_path} is missing VOLUME_ID field')
                            break
                        geo_file_spec = row.get('FILE_SPECIFICATION_NAME',
                                                None)
                        if geo_file_spec is None:
                            import_util.log_nonrepeating_error(
                f'{assoc_label_path} is missing FILE_SPECIFICATION_NAME field')
                            break
                        geo_full_file_spec = geo_vol+'/'+geo_file_spec
                        geo_pdsfile = pdsfile.PdsFile.from_filespec(
                                                geo_full_file_spec)
                        key = geo_pdsfile.opus_id
                        if key is None:
                            import_util.log_nonrepeating_error(
            f'Failed to convert file_spec "{geo_full_file_spec}" to opus_id '+
            f'for {assoc_label_path}')
                            continue
                        key = key.replace('.', '-')
                        # WARNING: HACK FOR VIMS XXX
                        # The current VIMS geo and inventory tables have entries
                        # for VIS and IR but the only way to distinguish
                        # between them is to look at the deprecated
                        # RING_OBSERVATION_ID. When Mark fixes this,
                        # there will be a sub-instrument column of some
                        # kind to use instead.
                        if geo_vol.startswith('COVIMS'):
                            ring_obs_id = row.get('RING_OBSERVATION_ID',
                                                  None)
                            if ring_obs_id is None:
                                import_util.log_nonrepeating_error(
                f'{assoc_label_path} is missing RING_OBSERVATION_ID field')
                                break
                            if ring_obs_id.endswith('IR'):
                                key += '_ir'
                            elif ring_obs_id.endswith('VIS'):
                                key += '_vis'
                            else:
                                import_util.log_nonrepeating_error(
                 f'{assoc_label_path} has bad RING_OBSERVATION_ID for '+
                 f'file_spec "{geo_full_file_spec}"')
                        if (assoc_type == 'ring_geo' or
                            assoc_type == 'inventory'):
                            # RING_GEO is easy - there is at most a single entry
                            # per observation, so we just create a dictionary
                            # keyed by opus_id.
                            assoc_dict[key] = row
                        elif assoc_type == 'body_surface_geo':
                            # SURFACE_GEO is more complicated, because there can
                            # be more than one entry per observation, since
                            # there is one for each target. We create a
                            # dictionary keyed by opus_id containing a
                            # dictionary keyed by target nameso we can collect
                            # all the target entries in one place.
                            key2 = row.get('TARGET_NAME', None)
                            if key2 is None:
                                import_util.log_nonrepeating_error(
                        f'{assoc_label_path} is missing TARGET_NAME field')
                                break
                            if key not in assoc_dict:
                                assoc_dict[key] = {}
                            assoc_dict[key][key2] = row
                else:
                    assert assoc_type == 'supp_index'
                    if instrument_name == 'COUVIS':
                        # The COUVIS supplemental index is keyed by a
                        # combination of VOLUME_ID (COUVIS_0001) and
                        # FILE_SPECIFICATION_NAME
                        # (DATA/D2000_153/EUV2000_153_15_52.LBL)
                        # that maps to the FILE_NAME field in the main index
                        # (/COUVIS_0001/DATA/D2000_153/EUV2000_153_15_52.LBL).
                        # It's too much of a pain to calculate the opus_id
                        # here to use that as a key, so we just stick with the
                        # filename.
                        for row in assoc_rows:
                            key1 = row.get('VOLUME_ID', None)
                            key2 = row.get('FILE_SPECIFICATION_NAME',
                                           None).upper()
                            if key1 is None or key2 is None:
                                import_util.log_nonrepeating_error(
                        f'{assoc_label_path} is missing VOLUME_ID or '+
                         'FILE_SPECIFICATION_NAME fields')
                                break
                            key = f'/{key1}/{key2}'
                            assoc_dict[key] = row
                    elif instrument_name == 'VGISS':
                        # The VGISS supplemental index is keyed by
                        # FILE_SPECIFICATION_NAME
                        # (DATA/C13854XX/C1385455_RAW.LBL)
                        # that maps to the FILE_SPECIFICATION_NAME field in the
                        # main index.
                        # It's too much of a pain to calculate the opus_id
                        # here to use that as a key, so we just stick with the
                        # filename.
                        for row in assoc_rows:
                            key = row.get('FILE_SPECIFICATION_NAME', None)
                            if key is None:
                                import_util.log_nonrepeating_error(
                f'{assoc_label_path} is missing FILE_SPECIFICATION_NAME field')
                                break
                            assoc_dict[key] = row
                    elif instrument_name in ['NHLORRI', 'NHMVIC']:
                        # The NHLORRI/NHMVIC supplemental index is keyed by
                        # FILE_SPECIFICATION_NAME
                        # (data/20070108_003059/lor_0030598439_0x630_eng.lbl)
                        # that maps to the PATH_NAME and FILE_NAME fields in the
                        # main index.
                        # It's too much of a pain to calculate the opus_id
                        # here to use that as a key, so we just stick with the
                        # filename.
                        for row in assoc_rows:
                            key = row.get('FILE_SPECIFICATION_NAME', None)
                            key = key.upper()
                            if key is None:
                                import_util.log_nonrepeating_error(
                f'{assoc_label_path} is missing FILE_SPECIFICATION_NAME field')
                                break
                            assoc_dict[key] = row
                    else:
                        # Something else uses a supplemental index that we
                        # haven't prepared for!
                        assert False
                metadata[assoc_type] = assoc_dict
                metadata[assoc_type+'_label'] = assoc_label_dict

    # Check to see if we have ring or surface geo when the instrument
    # supports it - but it isn't a fatal error if we don't because sometimes
    # the geo files are generated later
    if ('ring_geo' not in metadata and
        instrument_name in INSTRUMENTS_WITH_RING_GEO):
        impglobals.LOGGER.log('warning',
            f'Volume "{volume_id}" is missing ring geometry files')
    if ('body_surface_geo' not in metadata and
        instrument_name in INSTRUMENTS_WITH_SURFACE_GEO):
        impglobals.LOGGER.log('warning',
            f'Volume "{volume_id}" is missing body surface geometry files')

    if 'ring_geo' in metadata:
        li = len(metadata['index'])
        lr = len(metadata['ring_geo'])
        diff = li - lr
        if diff:
            impglobals.LOGGER.log('debug',
                f'Volume "{volume_id}" is missing {diff} RING_GEO entries '+
                f'({li} vs. {lr})')

    if 'index' in metadata and 'supp_index' in metadata:
        li = len(metadata['index'])
        ls = len(metadata['supp_index'])
        if li != ls:
            impglobals.LOGGER.log('warning',
                f'Volume "{volume_id}" has {li} index entries but '+
                f'{ls} supplemental_index entries')

    table_schemas, table_names_in_order = create_tables_for_import(volume_id,
                                                                   'import')

    # It's time to actually compute the values that will go in the database!
    # Start with the obs_general table, because other tables reference
    # it with foreign keys. Then do general, mission, and instrument in that
    # order so that each row processor can access values computed for the
    # more general ones. Later come things like type_id, wavelength, and
    # ring_geo. Finally take care of surface geometry, which has to be done
    # once for each target in the image, so is handled separately.
    table_rows = {}
    for table_name in table_names_in_order:
        table_rows[table_name] = []

    # Keep track of opus_id and check for duplicates. This happens, e.g.
    # in GOSSI every now and then. Yuck.
    used_opus_id_this_vol = set()

    # Also look for duplicates in the existing import tables
    if impglobals.ARGUMENTS.import_check_duplicate_id:
        used_opus_id_prev_vol = read_existing_import_opus_id()
    else:
        used_opus_id_prev_vol = set()

    used_targets = set()


    ##############################################
    ### MASTER LOOP - IMPORT ONE ROW AT A TIME ###
    ##############################################

    volume_type = VOLUME_ID_ROOT_TO_TYPE[volume_pdsfile.volset]

    for index_row_num, index_row in enumerate(obs_rows):
        metadata['index_row'] = index_row
        metadata['index_row_num'] = index_row_num+1
        obs_general_row = None
        obs_pds_row = None
        impglobals.CURRENT_INDEX_ROW_NUMBER = index_row_num+1

        if 'supp_index' in metadata and instrument_name == 'COUVIS':
            # Match up the FILENAME with the key we generated earlier
            couvis_filename = index_row['FILE_NAME'].upper()
            supp_index = metadata['supp_index']
            if couvis_filename in supp_index:
                metadata['supp_index_row'] = supp_index[couvis_filename]
            else:
                import_util.log_nonrepeating_error(
                    f'File "{couvis_filename}" is missing supplemental data')
                metadata['supp_index_row'] = None
                continue # We don't process entries without supp_index
        elif 'supp_index' in metadata and instrument_name == 'VGISS':
            # Match up the FILENAME
            filename = index_row['FILE_SPECIFICATION_NAME']
            supp_index = metadata['supp_index']
            if filename in supp_index:
                metadata['supp_index_row'] = supp_index[filename]
            else:
                import_util.log_nonrepeating_error(
                    f'File "{filename}" is missing supplemental data')
                metadata['supp_index_row'] = None
                continue # We don't process entries without supp_index
        elif ('supp_index' in metadata and
              instrument_name in ['NHLORRI', 'NHMVIC']):
            # Match up the PATH_NAME + FILE_NAME with FILE_SPECIFICATION_NAME
            path_name = index_row['PATH_NAME']
            file_name = index_row['FILE_NAME']
            file_spec_name = path_name+file_name
            file_spec_name = file_spec_name.upper()
            supp_index = metadata['supp_index']
            if file_spec_name in supp_index:
                metadata['supp_index_row'] = supp_index[file_spec_name]
            else:
                import_util.log_nonrepeating_error(
                    f'File "{file_spec_name}" is missing supplemental data')
                metadata['supp_index_row'] = None
                continue # We don't process entries without supp_index

        # Some index files, like for occultations, have rows that are for
        # products other than the data so we ignore any FILE_SPECIFICATION_NAME
        # that doesn't have 'DATA/' in it.
        # XXX We won't need this once we have the new index files.
        if volume_id.startswith('EBROCC'):
            filespec = metadata['index_row'].get('FILE_SPECIFICATION_NAME')
            if filespec is None and 'supp_index_row' in metadata:
                filespec = metadata['supp_index_row'].get(
                                                    'FILE_SPECIFICATION_NAME')
            if (filespec is None or
                (not filespec.lower().startswith('/data/') and
                 not filespec.lower().startswith('data/'))):
                continue

        # Sometimes a single row in the index turns into multiple opus_id
        # in the database. This happens with COVIMS because each observation
        # might include both VIS and IR entries. Build up a list of such entries
        # here and then process the row as many times as necessary.

        if instrument_name == 'COVIMS':
            phase_names = []
            if index_row['VIS_SAMPLING_MODE_ID'] != 'N/A':
                phase_names.append('VIS')
            if index_row['IR_SAMPLING_MODE_ID'] != 'N/A':
                phase_names.append('IR')
        else:
            phase_names = ['NORMAL']

        for phase_name in phase_names:
            metadata['phase_name'] = phase_name

            # Handle everything except surface_geo

            for table_num, table_name in enumerate(table_names_in_order):
                if table_name.startswith('obs_surface_geometry'):
                    # Deal with surface geometry a little later
                    continue
                if table_name == 'obs_files':
                    # obs_files is done separately below because it has multiple
                    # rows per observation
                    continue
                if table_name not in table_schemas:
                    # Table not relevant for this product
                    continue
                row = import_observation_table(volume_id,
                                               instrument_name,
                                               func_instrument_name,
                                               mission_abbrev,
                                               volume_type,
                                               table_name,
                                               table_schemas[table_name],
                                               metadata)
                if table_name == 'obs_pds':
                    obs_pds_row = row
                if table_name == 'obs_general':
                    obs_general_row = row
                    opus_id = row['opus_id']
                    if opus_id in used_opus_id_this_vol:
                        # Some of the GO_xxxx volumes have duplicate
                        # observations within a single volume. In these cases
                        # we have to use the NEW one and delete the old one.
                        impglobals.LOGGER.log('info',
                                f'Duplicate opus_id "{opus_id}" within '+
                                 'volume - removing previous one')
                        remove_opus_id_from_tables(table_rows,
                                                      opus_id)
                    used_opus_id_this_vol.add(opus_id)
                    if opus_id in used_opus_id_prev_vol:
                        # Some of the GO_xxxx and COUVIS_xxxx volumes have
                        # duplicate observations across volumes. In these cases
                        # we have to use the NEW one and delete the old one
                        # from the database itself. Note this will only be
                        # triggered if we have already loaded the previous
                        # volume into the import tables but not clear them out.
                        # In the case where we copied them to the perm tables
                        # and cleared them out, a future check during the copy
                        # process will catch the duplicates.
                        delete_opus_id_from_obs_tables(opus_id, 'import')
                table_rows[table_name].append(row)
                metadata[table_name+'_row'] = row

            assert obs_general_row is not None

            # Handle obs_surface_geometry_name and
            # obs_surface_geometry__<TARGET>

            if 'body_surface_geo' in metadata:
                surface_geo_dict = metadata['body_surface_geo']
                for table_name in table_names_in_order:
                    if not table_name.startswith('obs_surface_geometry_'):
                        # Deal with obs_surface_geometry_name and
                        # obs_surface_geometry__<TARGET> only
                        continue
                    # Here we are handling both obs_surface_geometry_name and
                    # obs_surface_geometry as well as all of the
                    # obs_surface_geometry__<TARGET> tables

                    # Retrieve the opus_id from obs_general to find the
                    # surface_geo
                    opus_id = obs_general_row['opus_id']
                    target_dict = surface_geo_dict.get(opus_id, {})
                    for target_name in sorted(target_dict.keys()):
                        used_targets.add(target_name)
                        # Note the following only affects
                        # obs_surface_geometry__<T> not the generalized
                        # obs_surface_geometry. This is fine because we want the
                        # generalized obs_surface_geometry to include all the
                        # targets.
                        new_target_name = import_util.table_name_for_sfc_target(
                                                                target_name)
                        new_table_name = table_name.replace('<TARGET>',
                                                            new_target_name)
                        metadata['body_surface_geo_row'] = target_dict[
                                                                target_name]

                        row = import_observation_table(volume_id,
                                                       instrument_name,
                                                       func_instrument_name,
                                                       mission_abbrev,
                                                       volume_type,
                                                       new_table_name,
                                                      table_schemas[table_name],
                                                       metadata)
                        if new_table_name not in table_rows:
                            table_rows[new_table_name] = []
                            impglobals.LOGGER.log('debug',
                f'Creating surface geo table for new target {new_target_name}')
                        table_rows[new_table_name].append(row)

            # Handle obs_surface_geometry
            # We have to do this later than the other obs_ tables because only
            # now do we know what all the available targets are

            surface_target_list = None
            if 'inventory' in metadata:
                inventory = metadata['inventory']
                if opus_id in inventory:
                    surface_target_list = inventory[opus_id]['TARGET_LIST']
            metadata['inventory_list'] = surface_target_list

            for table_name in table_names_in_order:
                if table_name != 'obs_surface_geometry':
                    # Deal with obs_surface_geometry only
                    continue
                # This is used to populate the surface geo target_list
                # field

                row = import_observation_table(volume_id,
                                               instrument_name,
                                               func_instrument_name,
                                               mission_abbrev,
                                               volume_type,
                                               table_name,
                                               table_schemas[table_name],
                                               metadata)
                if table_name not in table_rows:
                    table_rows[new_table_name] = []
                table_rows[table_name].append(row)

            # Handle obs_files

            if 'obs_files' not in table_rows:
                table_rows['obs_files'] = []
            for table_name in table_names_in_order:
                if table_name != 'obs_files':
                    # Deal with obs_files only
                    continue
                rows = get_pdsfile_rows_for_filespec(
                                obs_pds_row['primary_file_spec'],
                                obs_general_row['id'],
                                obs_general_row['opus_id'],
                                obs_general_row['volume_id'],
                                obs_general_row['instrument_id'])
                table_rows[table_name].extend(rows)


    #################################################################
    ### DONE COMPUTING ROW CONTENTS - CREATE MULTS AND DUMP TO DB ###
    #################################################################

    # Now that we have all the values, we have to dump out the mult tables
    # because they are referenced as foreign keys
    dump_import_mult_tables()

    # Now dump out the obs tables, in order, because at least obs_general
    # is referenced by foreign keys.
    for table_name in table_names_in_order:
        if table_name.find('<TARGET>') == -1:
            imp_name = impglobals.DATABASE.convert_raw_to_namespace(
                                'import', table_name)
            impglobals.LOGGER.log('debug',
                f'Inserting into obs table "{imp_name}"')
            impglobals.DATABASE.insert_rows('import', table_name,
                                            table_rows[table_name])
        else:
            for target_name in sorted(used_targets):
                new_table_name = table_name.replace(
                            '<TARGET>',
                            import_util.table_name_for_sfc_target(target_name))
                imp_name = impglobals.DATABASE.convert_raw_to_namespace(
                                    'import', new_table_name)
                impglobals.LOGGER.log('debug',
                    f'Inserting into obs table "{imp_name}"')
                surface_geo_schema = import_util.read_schema_for_table(
                                            'obs_surface_geometry_target',
                                            replace=[
            ('<TARGET>', import_util.table_name_for_sfc_target(target_name)),
            ('<SLUGTARGET>', import_util.slug_name_for_sfc_target(target_name))])
                # We can finally get around to creating the
                # obs_surface_geometry_<T> tables now that we know what targets
                # we have
                impglobals.DATABASE.create_table('import', new_table_name,
                                                 surface_geo_schema)
                impglobals.DATABASE.insert_rows('import', new_table_name,
                    table_rows[new_table_name])

    return True # SUCCESS!


def import_observation_table(volume_id,
                             instrument_name,
                             func_instrument_name,
                             mission_abbrev,
                             volume_type,
                             table_name,
                             table_schema,
                             metadata):
    "Import the data from a row from a PDS file into a single table."
    index_row = metadata['index_row']
    new_row = {}

    # So it can be accessed by populate_*
    metadata[table_name+'_row'] = new_row

    # Compute the columns in the specified order because some populate_*
    # functions rely on previous ones having already been run.

    ordered_table_schema = [(x.get('data_source_order', 0), x)
                            for x in table_schema]
    ordered_table_schema.sort(key=lambda x: x[0])
    ordered_columns = [x[1] for x in ordered_table_schema]

    # Run through all the based columns and compute their values.
    # Always skip "id" for tables other than obs_general, because this is just
    # an AUTO_INCREMENT field used by Django and has no other purpose.
    # Always skip "timestamp" fields because these are automatically filled in
    # by SQL because we mark them as ON CREATE and ON UPDATE.
    # Skip "mult_" tables because they are handled separately when the search
    # type is a GROUP-type.

    for table_column in ordered_columns:
        if table_column.get('put_mults_here', False):
            continue
        field_name = table_column['field_name']
        field_type = table_column['field_type']

        metadata['table_name'] = table_name
        metadata['field_name'] = field_name

        if field_name == 'timestamp':
            continue

        data_source_tuple = table_column.get('data_source', None)
        if not data_source_tuple:
            import_util.log_nonrepeating_warning(
                f'No data source for column "{field_name}" in table '+
                f'"{table_name}"')
            column_val = None
        else:

            ### COMPUTE THE NEW COLUMN VALUE ###

            column_val = None
            mult_label = None
            mult_label_set = False

            data_source_cmd, data_source_val = data_source_tuple
            if data_source_cmd.find(':') != -1:
                (data_source_cmd_prefix,
                 data_source_cmd_param) = data_source_cmd.split(':')
            else:
                data_source_cmd_prefix = data_source_cmd
                data_source_cmd_param = None

            if data_source_cmd_prefix == 'IGNORE':
                continue
            elif data_source_cmd_prefix == 'TAB':
                ref_index_row = None
                if data_source_cmd_param+'_row' not in metadata:
                    if data_source_cmd_param != 'ring_geo':
                        # If we don't have ring_geo for this volume, don't
                        # bitch about it because we already did earlier.
                        import_util.log_nonrepeating_error(
                    f'Unknown internal metadata name "{data_source_cmd_param}"'+
                    f' while processing column "{field_name}" in '+
                    f'table "{table_name}"')
                        continue
                else:
                    ref_index_row = metadata[data_source_cmd_param+'_row']
                if ref_index_row is not None:
                    if data_source_val not in ref_index_row:
                        import_util.log_nonrepeating_error(
                            f'Unknown referenced column "{data_source_val}" '+
                            f'while processing column "{field_name}" in '+
                            f'table "{table_name}"')
                        continue
                    column_val = import_util.safe_column(ref_index_row,
                                                         data_source_val)
            elif data_source_cmd_prefix == 'ARRAY':
                ref_index_name, array_index = data_source_cmd_param.split('.')
                array_index = int(array_index)
                ref_index_row = None
                if ref_index_name+'_row' not in metadata:
                    if data_source_cmd_param != 'ring_geo':
                        # If we don't have ring_geo for this volume, don't
                        # bitch about it because we already did earlier.
                        import_util.log_nonrepeating_error(
                    f'Unknown internal metadata name "{ref_index_name}"'+
                    f' while processing column "{field_name}" in '+
                    f'table "{table_name}"')
                        continue
                else:
                    ref_index_row = metadata[ref_index_name+'_row']
                if data_source_val not in ref_index_row:
                    import_util.log_nonrepeating_error(
                        f'Unknown PDS column "{data_source_val}" while '+
                        f'processing table "{table_name}"')
                    continue
                if (array_index < 0 or
                    array_index >= len(ref_index_row[data_source_val])):
                    import_util.log_nonrepeating_error(
                        f'Bad array index "{array_index}" for column '+
                        f'"{field_name}" in table "{table_name}"')
                    continue
                column_val = import_util.safe_column(ref_index_row,
                                                     data_source_val,
                                                     array_index)

            elif data_source_cmd_prefix == 'FUNCTION':
                ret = import_run_field_function(data_source_val,
                                                volume_id,
                                                instrument_name,
                                                func_instrument_name,
                                                mission_abbrev,
                                                volume_type,
                                                table_name,
                                                table_schema,
                                                metadata,
                                                field_name)
                if isinstance(ret, tuple) or isinstance(ret, list):
                    column_val, mult_label = ret
                    mult_label_set = True
                else:
                    column_val = ret
            elif data_source_cmd_prefix == 'MAX_ID':
                if table_name not in impglobals.MAX_TABLE_ID_CACHE:
                    impglobals.MAX_TABLE_ID_CACHE[table_name] = (
                        import_util.find_max_table_id(table_name))
                impglobals.MAX_TABLE_ID_CACHE[table_name] = (
                    impglobals.MAX_TABLE_ID_CACHE[table_name]+1)
                column_val = impglobals.MAX_TABLE_ID_CACHE[table_name]

            else:
                import_util.log_nonrepeating_error(
                    f'Unknown data_source type "{data_source_cmd}" for'+
                    f'"{field_name}" in table "{table_name}"')
                continue

        ### VALIDATE THE COLUMN VALUE ###

        if column_val is None:
            notnull = table_column.get('field_notnull', False)
            if notnull:
                import_util.log_nonrepeating_error(
                    f'Column "{field_name}" in table "{table_name}" '+
                    f'has NULL value but NOT NULL is set')
        else:
            if field_type.startswith('flag'):
                if column_val in [0, 'n', 'N', 'no', 'No', 'NO', 'off', 'OFF']:
                    if field_type == 'flag_onoff':
                        column_val = 'Off'
                    else:
                        column_val = 'No'
                elif column_val in [1, 'y', 'Y', 'yes', 'Yes', 'YES', 'on',
                                    'ON']:
                    if field_type == 'flag_onoff':
                        column_val = 'On'
                    else:
                        column_val = 'Yes'
                elif column_val in ['N/A', 'UNK', 'NULL']:
                    column_val = None
                else:
                    import_util.log_nonrepeating_error(
                        f'Column "{field_name}" in table "{table_name}" '+
                        f'has FLAG type but value "{column_val}" is not '+
                        f'a valid flag value')
                    column_val = None
            if field_type.startswith('char'):
                field_size = int(field_type[4:])
                if not isinstance(column_val, str):
                    import_util.log_nonrepeating_error(
                        f'Column "{field_name}" in table "{table_name}" '+
                        f'has CHAR type but value "{column_val}" is of '+
                        f'type "{type(column_val)}"')
                    column_val = ''
                elif len(column_val) > field_size:
                    import_util.log_nonrepeating_error(
                        f'Column "{field_name}" in table "{table_name}" '+
                        f'has CHAR size {field_size} but value '+
                        f'"{column_val}" is too long')
                    column_val = column_val[:field_size]
            elif (field_type.startswith('real') or
                  field_type.startswith('int') or
                  field_type.startswith('uint')):
                the_val = None
                if field_type.startswith('real'):
                    try:
                        the_val = float(column_val)
                    except ValueError:
                        import_util.log_nonrepeating_error(
                            f'Column "{field_name}" in table '+
                            f'"{table_name}" has REAL type but '+
                            f'"{column_val}" is not a float')
                        column_val = None
                else:
                    try:
                        the_val = int(column_val)
                    except ValueError:
                        import_util.log_nonrepeating_error(
                            f'Column "{field_name}" in table '+
                            f'"{table_name}" has INT type but '+
                            f'"{column_val}" is not an int')
                        column_val = None
                if column_val is not None and the_val is not None:
                    val_sentinel = table_column.get('val_sentinel', None)
                    if type(val_sentinel) != list:
                        val_sentinel = [val_sentinel]
                    if the_val in val_sentinel:
                        column_val = None
                        import_util.log_nonrepeating_error(
                            f'Caught sentinel value {the_val} for column '+
                            f'"{field_name}" that was missed'+
                            f' by the PDS label!')
                if column_val is not None and the_val is not None:
                    val_min = table_column.get('val_min', None)
                    val_max = table_column.get('val_max', None)
                    val_use_null = table_column.get('val_set_invalid_to_null',
                                                    False)
                    if val_min is not None and the_val < val_min:
                        if val_use_null:
                            msg = (f'Column "{field_name}" in table '+
                                   f'"{table_name}" has minimum value '+
                                   f'{val_min} but {column_val} is too small -'+
                                   f' substituting NULL')
                            impglobals.LOGGER.log('debug', msg)
                        else:
                            msg = (f'Column "{field_name}" in table '+
                                   f'"{table_name}" has minimum value '+
                                   f'{val_min} but {column_val} is too small')
                            import_util.log_nonrepeating_error(msg)
                        column_val = None
                    if val_max is not None and the_val > val_max:
                        if val_use_null:
                            msg = (f'Column "{field_name}" in table '+
                                   f'"{table_name}" has maximum value {val_max}'+
                                   f' but {column_val} is too large - '+
                                   f'substituting NULL')
                            impglobals.LOGGER.log('debug', msg)
                        else:
                            msg = (f'Column "{field_name}" in table '+
                                   f'"{table_name}" has maximum value '+
                                   f'{val_max} but {column_val} is too large')
                            import_util.log_nonrepeating_error(msg)
                        column_val = None

        new_row[field_name] = column_val

        ### CHECK TO SEE IF THERE IS AN ASSOCIATED MULT_ TABLE ###

        form_type = table_column.get('pi_form_type', None)
        if form_type is not None and form_type.find(':') != -1:
            form_type = form_type[:form_type.find(':')]
        if form_type in GROUP_FORM_TYPES:
            mult_column_name = import_util.table_name_mult(table_name,
                                                           field_name)
            if not mult_label_set:
                if column_val is None:
                    mult_label = 'N/A'
                else:
                    mult_label = str(column_val)
                    if (not mult_label[0].isdigit() or
                        not mult_label[-1].isdigit()):
                        # This catches things like 2014 MU69 and leaves them
                        # in all caps
                        mult_label = mult_label.title()
            id_num = update_mult_table(table_name, field_name, table_column,
                                       column_val, mult_label)
            new_row[mult_column_name] = id_num

        ### A BIT OF TRICKERY - WE CAN'T RETRIEVE THE RING OR SURFACE GEO
        ### METADATA UNTIL WE KNOW THE OPUS_ID, WHICH IS COMPUTED
        ### WHILE PROCESSING OBS_GENERAL

        if table_name == 'obs_general' and field_name == 'opus_id':
            opus_id = new_row[field_name]
            if 'ring_geo' in metadata:
                ring_geo = metadata['ring_geo'].get(opus_id, None)
                metadata['ring_geo_row'] = ring_geo
                if (ring_geo is None and
                    impglobals.ARGUMENTS.import_report_missing_ring_geo):
                    impglobals.LOGGER.log('warning',
                        f'RING GEO metadata available but missing for '+
                        f'opus_id "{opus_id}"')
            if 'body_surface_geo' in metadata:
                body_geo = metadata['body_surface_geo'].get(opus_id)
                metadata['body_surface_geo_row'] = body_geo

    return new_row

def import_run_field_function(func_name_suffix, volume_id,
                              instrument_name,
                              func_instrument_name,
                              mission_abbrev,
                              volume_type,
                              table_name, table_schema, metadata,
                              field_name):
    "Call the Python function used to populate a single field in a table."
    func_name = 'populate_'+func_name_suffix
    func_name = func_name.replace('<INST>', func_instrument_name)
    func_name = func_name.replace('<MISSION>', mission_abbrev)
    func_name = func_name.replace('<TYPE>', volume_type)
    if func_name not in globals():
        import_util.log_nonrepeating_error(
            f'Unknown table populate function "{func_name}" for '+
            f'"{field_name}" in table "{table_name}"')
        return None
    func = globals()[func_name]
    return func(volume_id=volume_id, instrument_name=instrument_name,
                mission_abbrev=mission_abbrev,
                table_name=table_name, table_schema=table_schema,
                metadata=metadata)

def _check_for_pdsfile_exception():
    if pdsfile.PdsFile.LAST_EXC_INFO != (None, None, None):
        trace_str = traceback.format_exception(*pdsfile.PdsFile.LAST_EXC_INFO)
        import_util.log_nonrepeating_error('PdsFile had internal error: '
                                           +''.join(trace_str))
        pdsfile.PdsFile.LAST_EXC_INFO = (None, None, None)

def get_pdsfile_rows_for_filespec(filespec, obs_general_id, opus_id, volume_id,
                                  instrument_id):
    rows = []

    try:
        pdsf = pdsfile.PdsFile.from_filespec(filespec)
        _check_for_pdsfile_exception()
    except ValueError:
        import_util.log_nonrepeating_error(
                                    f'Failed to convert file_spec "{file_spec}"')
        return

    products = pdsf.opus_products()
    _check_for_pdsfile_exception()
    if '' in products:
        file_list_str = '  '.join([x.abspath for x in products[''][0]])
        import_util.log_nonrepeating_warning(
                  f'Empty opus_product key for files: '+
                  file_list_str)
        del products['']
        _check_for_pdsfile_exception()
    # Keep a running list of all products by type, sorted by version
    for product_type in products:
        (category, sort_order_num, short_name, full_name) = product_type
        if category == 'standard':
            pref = 'ZZZZZ1'
        elif category == 'metadata':
            pref = 'ZZZZZ2'
        elif category == 'browse':
            pref = 'ZZZZZ3'
        elif category == 'diagram':
            pref = 'ZZZZZ4'
        else:
            pref_list = category.split(' ')
            pref = pref_list[0][:3]
            if len(pref_list) == 1:
                pref += pref_list[0][-3:]
            else:
                pref += pref_list[-1][:3]
            pref = pref.upper()
        sort_order = pref + ('%03d' % sort_order_num)
        list_of_sublists = products[product_type]
        for sublist in list_of_sublists:
            for file_num, file in enumerate(sublist):
                version_number = sublist[0].version_rank
                version_name = sublist[0].version_id
                if version_name == '':
                    version_name = 'Current'
                logical_path = file.logical_path
                url = file.url
                checksum = file.checksum
                size = file.size_bytes
                width = file.width or None
                height = file.height or None

                if 'obs_files' not in impglobals.MAX_TABLE_ID_CACHE:
                    impglobals.MAX_TABLE_ID_CACHE['obs_files'] = (
                        import_util.find_max_table_id('obs_files'))
                impglobals.MAX_TABLE_ID_CACHE['obs_files'] = (
                    impglobals.MAX_TABLE_ID_CACHE['obs_files']+1)
                id = impglobals.MAX_TABLE_ID_CACHE['obs_files']

                row = {'id': id,
                       'obs_general_id': obs_general_id,
                       'opus_id': opus_id,
                       'volume_id': volume_id,
                       'instrument_id': instrument_id,
                       'version_number': version_number,
                       'version_name': version_name,
                       'category': category,
                       'sort_order': sort_order,
                       'product_order': file_num,
                       'short_name': short_name,
                       'full_name': full_name,
                       'logical_path': logical_path,
                       'url': url,
                       'checksum': checksum,
                       'size': size,
                       'width': width,
                       'height': height
                      }
                rows.append(row)
                _check_for_pdsfile_exception()

    return rows

def remove_opus_id_from_tables(table_rows, opus_id):
    for table_name in table_rows:
        rows = table_rows[table_name]
        i = 0
        while i < len(rows):
            if ('opus_id' in rows[i] and
                rows[i]['opus_id'] == opus_id):
                impglobals.LOGGER.log('debug',
                    f'Removing "{opus_id}" from unwritten table '+
                    f'"{table_name}"')
                del rows[i]
                continue # There might be more than one in obs_surface_geometry
            i += 1


################################################################################
# THE MAIN IMPORT LOOP
################################################################################

def do_import_steps():
    "Do all of the steps requested by the user for an import."
    impglobals.IMPORT_HAS_BAD_DATA = False
    impglobals.MAX_TABLE_ID_CACHE = {}
    global _CREATED_IMP_MULT_TABLES
    _CREATED_IMP_MULT_TABLES = set()

    volume_id_list = []
    for volume_id in import_util.yield_import_volume_ids(
                                                    impglobals.ARGUMENTS):
        volume_id_list.append(volume_id)

    # Delete the old import tables if
    #   --drop-old-import-tables but not
    #   --leave-old-import-tables
    # is given.
    old_imp_tables_dropped = False
    if (impglobals.ARGUMENTS.drop_old_import_tables and
        not impglobals.ARGUMENTS.leave_old_import_tables):
        impglobals.LOGGER.log('info', 'Deleting all old import tables')
        delete_all_obs_mult_tables('import')
        old_imp_tables_dropped = True

    # If --drop-permanent-tables AND --scorched-earth is given, delete
    # the permanent tables entirely. We have to do this before starting the
    # real import so that there are no vestigial mult tables and the ids
    # can be reset to 0.
    old_perm_tables_dropped = False
    if (impglobals.ARGUMENTS.drop_permanent_tables and
        impglobals.ARGUMENTS.scorched_earth):
        impglobals.LOGGER.log('warning', '** DELETING ALL PERMANENT TABLES **')
        delete_all_obs_mult_tables('perm')
        old_perm_tables_dropped = True

        # This must be done after the permanent tables were deleted, since
        # that process also deleted these tables. In this case, we didn't do
        # this step earlier.
        if (impglobals.ARGUMENTS.drop_cache_tables or
            impglobals.ARGUMENTS.create_cart):
            impglobals.LOGGER.open(
                f'Cleaning up OPUS/Django tables',
                limits={'info': impglobals.ARGUMENTS.log_info_limit,
                        'debug': impglobals.ARGUMENTS.log_debug_limit})

            if impglobals.ARGUMENTS.create_cart:
                do_cart.create_cart()
            if impglobals.ARGUMENTS.drop_cache_tables:
                do_django.drop_cache_tables()

            impglobals.LOGGER.close()

    # If --import is given, first delete the volumes from the import tables,
    # then do the new import
    if (impglobals.ARGUMENTS.do_import or
        impglobals.ARGUMENTS.delete_import_volumes):
        if not old_imp_tables_dropped:
            impglobals.LOGGER.log('warning',
                                  'Importing on top of previous import tables!')
            for volume_id in import_util.yield_import_volume_ids(
                                                    impglobals.ARGUMENTS):
                delete_volume_from_obs_tables(volume_id, 'import')

    if impglobals.ARGUMENTS.do_import:
        for volume_id in volume_id_list:
            if not import_one_volume(volume_id):
                impglobals.LOGGER.log('fatal',
                        f'Import of volume {volume_id} failed - Aborting')
                impglobals.IMPORT_HAS_BAD_DATA = True

        if (impglobals.IMPORT_HAS_BAD_DATA and
            not impglobals.ARGUMENTS.import_ignore_errors):
            impglobals.LOGGER.log('fatal',
                                  'ERRORs found during import - aborting early')
            return False

    # If --copy-import-to-permanent-tables or --delete-permanent-import-volumes
    # are given, delete all volumes that exist in the import tables from the
    # permanent tables.
    import_volume_ids = []
    if ((impglobals.ARGUMENTS.copy_import_to_permanent_tables or
         impglobals.ARGUMENTS.delete_permanent_import_volumes) and
        impglobals.DATABASE.table_exists('import', 'obs_general')):
        imp_obs_general_table_name = (
            impglobals.DATABASE.convert_raw_to_namespace('import',
                                                         'obs_general'))
        q = impglobals.DATABASE.quote_identifier
        import_volume_ids = [x[0] for x in
                      impglobals.DATABASE.general_select(
    f'DISTINCT {q("volume_id")} FROM {q(imp_obs_general_table_name)} ORDER BY {q("volume_id")}')
                     ]
        if not old_perm_tables_dropped:
            # Don't bother if there's nothing there!
            if not impglobals.ARGUMENTS.create_cart:
                impglobals.LOGGER.log('warning',
        'Deleting volumes from perm tables but cart table not wiped')
            for volume_id in import_volume_ids:
                delete_volume_from_obs_tables(volume_id, 'perm')

    # If --delete-permanent-volumes is given, delete the given set of volumes
    # from the permanent tables.
    if impglobals.ARGUMENTS.delete_permanent_volumes:
        for volume_id in volume_id_list:
            delete_volume_from_obs_tables(volume_id, 'perm')

    # If --copy-import-to-permanent-tables is given, create obs and mult tables
    # as necessary, then copy the import tables to the permanent tables.
    if impglobals.ARGUMENTS.copy_import_to_permanent_tables:
        for volume_id in import_volume_ids:
            create_tables_for_import(volume_id, 'perm')

        impglobals.LOGGER.log('info', 'Deleting duplicate opus_ids')
        delete_duplicate_opus_id_from_perm_tables()

        impglobals.LOGGER.log('info', 'Copying import mult tables to permanent')
        copy_mult_from_import_to_permanent()

        for volume_id in import_volume_ids:
            copy_volume_from_import_to_permanent(volume_id)

    # If --drop-new-import-tables is given, delete the new import tables
    if impglobals.ARGUMENTS.drop_new_import_tables:
        impglobals.LOGGER.log('info', 'Deleting all new import tables')
        delete_all_obs_mult_tables('import')

    # If --analyze-permanent-tables is given, analyze the permanent tables
    if impglobals.ARGUMENTS.analyze_permanent_tables:
        impglobals.LOGGER.log('info', 'Analyzing all permanent tables')
        analyze_all_tables('perm')
