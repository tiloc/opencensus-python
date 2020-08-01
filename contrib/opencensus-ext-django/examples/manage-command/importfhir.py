"""A Django admin command to import FHIR resources.

   IMPORTANT: This does not execute, but illustrates how an existing command can be instrumented to execute with tracemanage.py in a fully traced fashion. Look for "OC:" comments for some pointers.
"""
import os
import glob
import itertools
import logging  # OC: Normal Python logging is used.
import json

from django.core.management.base import BaseCommand, CommandError, no_translations
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from opencensus.trace import execution_context
from opencensus.trace.tracer import Tracer

from fhirclient.models.resource import Resource
from fhirclient.models.bundle import Bundle, BundleEntry
import fhirclient.models.patient as pat
import fhirclient.models.condition as cond
import fhirclient.models.observation as obs
import fhirclient.models.coding as cd
import fhirclient.models.codeableconcept as cc
import fhirclient.models.humanname as hn


from healthmodels.models import Patient, PatientIdentifier, Condition, CompoundObservation, Observation, CodeableConcept, HumanName # pylint: disable=E0401

# OC: Normal logger
logger = logging.getLogger(__name__)


# See https://docs.djangoproject.com/en/2.1/howto/custom-management-commands/
class Command(BaseCommand):
    """Django admin command to import FHIR resources from files"""
    help = "Import FHIR resources from a JSON file. Resources need to be formatted as a Bundle"

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='+', help='Path of a file or a folder of files.')
        parser.add_argument('-e', '--extension', default='.json', help='File extension to filter by.')
        parser.add_argument('-r', '--recursive', action='store_true', default=False, help='Search through subfolders')
        parser.add_argument("--strict", action='store_true', default=False, help='Should parsing of FHIR resources be strict?')
        parser.add_argument("--force", action='store_true', default=False, help='Should issues be ignored as much as possible?')


    def hastypecode(self, identifier, code):
        """Determines whether a FHIR Identifier has a specific type code.
        """
        if identifier.type is None:
            return False
        return identifier.type.coding[0].code == code


    def get_codeableconcept(self, codeableconcept):
        """Convert a FHIR CodeableConcept into a Django Health CodeableConcept"""
        return CodeableConcept(codeableconcept.coding[0].code, codeableconcept.coding[0].system, codeableconcept.coding[0].display) if not codeableconcept is None else None

    def get_humanname(self, humanname):
        """Convert a FHIR HumanName into a Django Health HumanName"""
        return HumanName(humanname.family, 
                         " ".join(iter(humanname.given)) if humanname.given is not None else None,
                         " ".join(iter(humanname.prefix)) if humanname.prefix is not None else None, 
                         " ".join(iter(humanname.suffix)) if humanname.suffix is not None else None)


    def import_patient(self, res, options):
        """Import a single patient"""
        verbosity = options['verbosity']
        strict = options['strict']
        force = options['force']

        f_patient = pat.Patient(res, strict=strict)

        # OC: Normal logging for progress. Do not write to stdout or stderr as some sample code for manage commands does.
        logger.info(f'Importing {f_patient}')

        official_name = [x for x in f_patient.name if x.use == 'official'][0]                # pylint: disable=E1133
        # TODO: Pull type code from settings
        primary_identifier = [x for x in f_patient.identifier if self.hastypecode(x, 'MR')][0]    # pylint: disable=E1133

        d_patient = Patient(
            id=f_patient.id,
            identifier=primary_identifier.value,
            name=self.get_humanname(official_name),
            gender=f_patient.gender,
            birth_date=f_patient.birthDate.isostring
        )

        # TODO: This is currently not working as it should. Problems with updating existing entries and created_at NOT NULL constraints
        # Do I need to .create() the object first, before saving it???
        try:
            d_patient.full_clean(validate_unique=False)
        except ValidationError as err:
            # OC: Error logging with exceptions will also be sent to telemetry
            logger.error('Validation failed', exc_info=True)

        try:
            d_patient.save()
        except IntegrityError as err:
            logger.error('Saving to database failed', exc_info=True)

        # TODO: This should use bulk_create
        for f_patid in f_patient.identifier:
            d_patid = PatientIdentifier(
                type_code=self.get_codeableconcept(f_patid.type),
                system=f_patid.system,
                value=f_patid.value,
                subject_id=f_patient.id          
            )
            try:
                d_patid.save()
            except IntegrityError as err:
                logger.error('Saving patient identifier to database failed', exc_info=True)

    def import_condition(self, res, options):
        """Import a single condition"""
        verbosity = options['verbosity']
        strict = options['strict']
        force = options['force']

        f_condition = cond.Condition(res, strict=strict)

        logger.info(f'Importing {f_condition}')

        d_condition = Condition(
            code=self.get_codeableconcept(f_condition.code),
            onset_datetime=f_condition.onsetDateTime.date,
            subject_id=f_condition.subject.reference[9:]  # Remove urn:uuid: prefix
        )

        try:
            d_condition.save()
        except IntegrityError as err:
            logger.error('Saving condition to database failed', exc_info=True)



    def import_observation(self, res, options):
        """Import a single observation"""
        verbosity = options['verbosity']
        strict = options['strict']
        force = options['force']

        f_observation = obs.Observation(res, strict=strict)

        logger.info(f'Importing {f_observation}')

        if f_observation.component is None:
            logger.debug('No components. Importing as single observation.')

            d_observation = None
            if f_observation.valueQuantity is not None:
                d_observation = Observation(
                    code=self.get_codeableconcept(f_observation.code),
                    category=self.get_codeableconcept(next(iter(f_observation.category))) if f_observation.category is not None else None,
                    comment=f_observation.comment if f_observation.comment is not None else '',
                    effective_datetime=f_observation.effectiveDateTime.date,
                    value_quantity_value=f_observation.valueQuantity.value,
                    value_quantity_unit=CodeableConcept(f_observation.valueQuantity.code, f_observation.valueQuantity.system, f_observation.valueQuantity.unit),
                    # See: https://docs.djangoproject.com/en/2.1/topics/db/optimization/#use-foreign-key-values-directly
                    subject_id=f_observation.subject.reference[9:]  # Remove urn:uuid: prefix
                )

            if d_observation is not None:
                try:
                    d_observation.save()
                except IntegrityError as err:
                    logger.error('Saving single observation to database failed', exc_info=True)
        else:
            logger.debug('Multiple components. Importing as compound observation with children.')

            cc_observation = CompoundObservation(
                code=self.get_codeableconcept(f_observation.code)
            )

            try:
                cc_observation.save()
            except IntegrityError as err:
                logger.error(f'Saving compound observation to database failed', exc_info=True)

            for a_component in f_observation.component: # pylint: disable=E1133
                d_observation = None
                d_observation = Observation(
                    code=self.get_codeableconcept(a_component.code),
                    category=self.get_codeableconcept(next(iter(f_observation.category))) if f_observation.category is not None else None,
                    comment=f_observation.comment if f_observation.comment is not None else '',
                    effective_datetime=f_observation.effectiveDateTime.date,
                    value_quantity_value=a_component.valueQuantity.value,
                    value_quantity_unit=CodeableConcept(a_component.valueQuantity.code, a_component.valueQuantity.system, a_component.valueQuantity.unit),
                    # See: https://docs.djangoproject.com/en/2.1/topics/db/optimization/#use-foreign-key-values-directly
                    subject_id=f_observation.subject.reference[9:],  # Remove urn:uuid: prefix
                    compound_observation=cc_observation
                )

                try:
                    d_observation.save()
                except IntegrityError as err:
                    logger.error(f'Saving child observation to database failed', exc_info=True)



    def import_collection(self, filepath, options):
        """Iterate over a collection bundle and dispatch to the importers for the various resource types"""
        verbosity = options['verbosity']
        strict = options['strict']

        logger.info(f'Importing {filepath}')
        with open(filepath, 'r') as handle:
            fhirjs = json.load(handle)
            bundle = Bundle(fhirjs, strict=strict)

            for entry in fhirjs["entry"]:   # TODO: Is there something more elegant than mucking through a JSON structure?
                res = entry["resource"]     # TODO: Is there something more elegant than mucking through a JSON structure?
                res_type = res["resourceType"]

                # OC: Creating spans from the tracer of the execution context will divide the overall execution into small, measurable chunks
                tracer = execution_context.get_opencensus_tracer()
                with tracer.span(name=f'Import {res_type}'):
                    logger.debug(f'Resource: {res}')

                    if res_type == "Patient":
                        self.import_patient(res, options)

                    if res_type == "Condition":
                        self.import_condition(res, options)

                    if res_type == "Observation":
                        self.import_observation(res, options)


    @no_translations                
    def handle(self, *args, **options):
        # Parse paths
        searchpath = options['path']
        extension = options['extension']
        recursive = options['recursive']
        verbosity = options['verbosity']

        # OC: Converting verbosity parameter into a log level
        if verbosity == 0:
            logger.setLevel(logging.WARNING)
        elif verbosity == 1:
            logger.setLevel(logging.INFO)
        else:
            logger.setLevel(logging.DEBUG)

        # TODO: This code is good enough for now, but could be cleaned up
        # TODO: Work consistently with set(). Consistently recurse (can the current code even go beyond 1 level deep?)
        full_paths = list(itertools.chain(*[glob.glob(os.path.join(os.getcwd(), path)) for path in searchpath]))
        logger.debug(f'Looking in {full_paths}')

        files = set()
        for path in full_paths:
            if os.path.isfile(path) and extension == os.path.splitext(path)[-1].lower():
                files.add(path)
            else:
                if recursive:
                    files |= set(glob.glob(path + '/*' + extension))

        for a_file in files:
            self.import_collection(a_file, options)


