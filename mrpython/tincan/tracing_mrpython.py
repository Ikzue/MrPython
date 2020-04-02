import threading, os, tokenize
import hashlib, uuid, getpass
import tincan.tracing_io as io
import copy
from tincan import (
    RemoteLRS,
    Statement,
    Agent,
    Verb,
    Activity,
    Context,
    Result,
    LanguageMap,
    ActivityDefinition,
    StateDocument,
    lrs_properties,
)
from translate import tr


def create_actor():
    student_hash = io.get_student_hash()
    student_id = "https://www.lip6.fr/mocah/invalidURI/student-number:" + student_hash
    return Agent(openid=student_id, name=student_hash)


def update_actor():
    global actor
    actor = create_actor()


def init_student_number():
    """if the student number hash isn't initialized, we use the student number in the OS username.
    If the OS username is a 7 digit integer, we hash it and keep it.
    Otherwise we keep the default.
    """
    student_hash = io.get_student_hash()
    if student_hash == "not-initialized":
        new_hash = None
        os_username = getpass.getuser()
        if os_username.isnumeric() and len(os_username) == 7:
            student_number = int(os_username)
            modify_student_number(str(student_number))
            return
        io.modify_student_hash("default")


def modify_student_number(student_number):
    """
    Keep a hash of the student number
    """
    old_hash = io.get_student_hash()
    m = hashlib.sha1(str.encode(student_number))
    new_hash = m.hexdigest()[:10]
    io.modify_student_hash(new_hash)
    send_statement("updated", "student-number",
                   {"https://www.lip6.fr/mocah/invalidURI/old-hash": old_hash,
                    "https://www.lip6.fr/mocah/invalidURI/new_hash": new_hash})
    update_actor()




def clear_stack():
    """Clear the stack of statements"""
    statement = io.get_statement()
    while statement:  # While there are statements
        statement = Statement.from_json(statement)
        if send_statement_lrs(statement):  # If the statement could have been sent to the LRS
            io.remove_statement()
            statement = io.get_statement()
        else:
            return


def send_statement_lrs(statement):
    try:
        response = lrs.save_statement(statement)
    except OSError as e:
        print("Tracing: Couldn't connect to the LRS: {}".format(e))
        return False
    if not response:
        print("Tracing: statement failed to save")
        return False
    elif response and not response.success:
        print("Tracing: Statement request could not be sent to the LRS: {}".format(response.data))
        return False
    else:
        print("Tracing: Statement request has been sent properly to the LRS, Statement ID is {}".format(
            response.data))
        return True


def send_statement(verb, activity, activity_extensions=None):
    """Send a statement with the verb and activity keys and the context extensions"""
    def thread_function(verb, activity, activity_extensions):
        #Create the statement from the actor, verb, object and potentially the context
        if verb not in verbs:
            print("Tracing: Missing verb key {}".format(verb))
            return
        if activity not in activities:
            print("Tracing: Missing activity key {}".format(activity))
            return
        print("Tracing: Creating and Sending statement, {} {}".format(verb, activity))
        verb = verbs[verb]
        object = activities[activity]
        extensions = {"https://www.lip6.fr/mocah/invalidURI/extensions/session": session}
        context = Context(extensions=extensions)

        if activity_extensions:
            object = copy.deepcopy(object)
            object.definition.extensions = activity_extensions

        statement = Statement(
            actor=actor,
            verb=verb,
            object=object,
            context=context
        )
        # Send statement and receive HTTP response
        #if not send_statement_lrs(statement):
        #    io.add_statement(statement)

    # Send the statement from another thread
    x = threading.Thread(target=thread_function, args=(verb, activity, activity_extensions))
    x.start()


def add_extensions_error(error, error_class, filename = None, instruction = None):
    extensions = {"https://www.lip6.fr/mocah/invalidURI/extensions/error-severity": error.severity,  # warning or error
                  "https://www.lip6.fr/mocah/invalidURI/extensions/error-type": error.err_type,
                  "https://www.lip6.fr/mocah/invalidURI/extensions/error-class": error_class,
                  "https://www.lip6.fr/mocah/invalidURI/extensions/error-message": error.error_details(),
                  "https://www.lip6.fr/mocah/invalidURI/extensions/error-instruction": instruction,
                  "https://www.lip6.fr/mocah/invalidURI/extensions/error-line": error.line
                  }
    if filename:
        extensions["https://www.lip6.fr/mocah/invalidURI/extensions/filename"] = os.path.basename(filename)
    return extensions


def send_statement_execute(report, mode, filename):
    """Create and send a statement at the end of the execution of the students program"""
    with tokenize.open(filename) as fp:
        source = fp.read()
        source = source.split('\n')
    # Send statements for all errors
    nb_errors = 0
    nb_warnings = 0
    for error in report.convention_errors:
        if error.severity == "error":
            nb_errors += 1
            activity = "execution-error"
        elif error.severity == "warning":
            nb_warnings += 1
            activity = "execution-warning"
        else:
            continue
        if error.line:
            instruction = source[error.line-1]
        else:
            instruction = "None"
        extensions = add_extensions_error(error, "typechecking", filename, instruction)
        send_statement("had", activity, activity_extensions=extensions)

    for error in report.compilation_errors:
        if error.severity == "error":
            nb_errors += 1
            activity = "execution-error"
        elif error.severity == "warning":
            nb_warnings += 1
            activity = "execution-warning"
        else:
            continue
        if error.line:
            instruction = source[error.line-1]
        else:
            instruction = "None"
        extensions = add_extensions_error(error, "compilation", filename, instruction)
        nb_errors += 1
        send_statement("had", activity, activity_extensions=extensions)

    for error in report.execution_errors:
        if error.severity == "error":
            nb_errors += 1
            activity = "execution-error"
        elif error.severity == "warning":
            nb_warnings += 1
            activity = "execution-warning"
        else:
            continue
        if error.line:
            instruction = source[error.line-1]
        else:
            instruction = "None"
        extensions = add_extensions_error(error, "execution", filename, instruction)
        nb_errors += 1
        send_statement("had", "execution-error", activity_extensions=extensions)

    #  Send final statement: Execution passed or failed
    if nb_errors == 0:
        verb = "passed"
    else:
        verb = "failed"
    extensions = {"https://www.lip6.fr/mocah/invalidURI/extensions/mode": mode,
                  "https://www.lip6.fr/mocah/invalidURI/extensions/filename": os.path.basename(filename),
                  "https://www.lip6.fr/mocah/invalidURI/extensions/nb-errors": nb_errors,
                  "https://www.lip6.fr/mocah/invalidURI/extensions/nb-warnings": nb_warnings}
    if not (report.has_compilation_error() or report.has_execution_error()) and mode == tr("student") and report.nb_defined_funs > 0:
        extensions["https://www.lip6.fr/mocah/invalidURI/extensions/number-asserts"] = report.nb_passed_tests
    else:
        extensions["https://www.lip6.fr/mocah/invalidURI/extensions/number-asserts"] = "unchecked"
    send_statement(verb, "execution", activity_extensions=extensions)


def send_statement_evaluate(report, mode, instruction):
    """Create and send a statement at the end of the execution of the students program"""

    # Send statements for all errors
    nb_errors = 0
    nb_warnings = 0
    for error in report.convention_errors:
        if error.severity == "error":
            nb_errors += 1
            activity = "evaluation-error"
        elif error.severity == "warning":
            nb_warnings += 1
            activity = "evaluation-warning"
        else:
            continue
        extensions = add_extensions_error(error, "typechecking", instruction=instruction)
        send_statement("had", activity, activity_extensions=extensions)

    for error in report.compilation_errors:
        if error.severity == "error":
            nb_errors += 1
            activity = "evaluation-error"
        elif error.severity == "warning":
            nb_warnings += 1
            activity = "evaluation-warning"
        else:
            continue
        extensions = add_extensions_error(error, "compilation", instruction=instruction)
        send_statement("had", activity, activity_extensions=extensions)

    for error in report.execution_errors:
        if error.severity == "error":
            nb_errors += 1
            activity = "evaluation-error"
        elif error.severity == "warning":
            nb_warnings += 1
            activity = "evaluation-warning"
        else:
            continue
        extensions = add_extensions_error(error, "execution", instruction=instruction)
        send_statement("had", activity, activity_extensions=extensions)


    #  Send final statement: Evaluation passed or failed
    if nb_errors == 0:
        verb = "passed"
    else:
        verb = "failed"
    extensions = {"https://www.lip6.fr/mocah/invalidURI/extensions/mode": mode,
                  "https://www.lip6.fr/mocah/invalidURI/extensions/instruction": instruction,
                  "https://www.lip6.fr/mocah/invalidURI/extensions/nb-errors": nb_errors,
                  "https://www.lip6.fr/mocah/invalidURI/extensions/nb-warnings": nb_warnings}
    send_statement(verb, "evaluation", activity_extensions=extensions)


lrs = RemoteLRS(
    version=lrs_properties.version,
    endpoint=lrs_properties.endpoint,
    username=lrs_properties.username,
    password=lrs_properties.password)
init_student_number()
actor = create_actor()
session = str(uuid.uuid4())[:10]
verbs = {
    "opened": Verb(
        id="http://activitystrea.ms/schema/1.0/open", display=LanguageMap({'en-US': 'opened'})),
    "closed": Verb(
        id="http://activitystrea.ms/schema/1.0/close", display=LanguageMap({'en-US': 'closed'})),
    "updated": Verb(
        id="http://activitystrea.ms/schema/1.0/update", display=LanguageMap({'en-US': 'updated'})),
    "created": Verb(
        id="http://activitystrea.ms/schema/1.0/create", display=LanguageMap({'en-US': 'created'})),
    "saved": Verb(
        id="http://activitystrea.ms/schema/1.0/save", display=LanguageMap({'en-US': 'saved'})),
    "switched": Verb(
        id="https://www.lip6.fr/mocah/invalidURI/verbs/switched", display=LanguageMap({'en-US': 'switched'})),
    "started": Verb(
        id="http://activitystrea.ms/schema/1.0/start", display=LanguageMap({'en-US': 'started'})),
    "passed": Verb(
        id="http://adlnet.gov/expapi/verbs/passed", display=LanguageMap({'en-US': 'passed'})),
    "failed": Verb(
        id="http://adlnet.gov/expapi/verbs/failed", display=LanguageMap({'en-US': 'failed'})),
    "terminated": Verb(
        id="http://activitystrea.ms/schema/1.0/terminate", display=LanguageMap({'en-US': 'terminated'})),
    "had": Verb(
        id="https://www.lip6.fr/mocah/invalidURI/verbs/had", display=LanguageMap({'en-US': 'had'})),
    "typed": Verb(
        id="https://www.lip6.fr/mocah/invalidURI/verbs/typed", display=LanguageMap({'en-US': 'typed'})),
    "modified": Verb(
        id="https://www.lip6.fr/mocah/invalidURI/verbs/modified", display=LanguageMap({'en-US': 'modified'})),
    "moved": Verb(
        id="https://www.lip6.fr/mocah/invalidURI/verbs/moved", display=LanguageMap({'en-US': 'moved'})),
    "deleted": Verb(
        id="http://activitystrea.ms/schema/1.0/delete", display=LanguageMap({'en-US': 'deleted'})),
    "inserted": Verb(
        id="http://activitystrea.ms/schema/1.0/insert", display=LanguageMap({'en-US': 'inserted'})),
    }

activities = {
    "application": Activity(
        id="http://activitystrea.ms/schema/1.0/application",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'the MrPython application'}))),
    "student-number": Activity(
        id="https://www.lip6.fr/mocah/invalidURI/activity-types/student-number",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'his/her student number'}))),
    "file": Activity(
        id="http://activitystrea.ms/schema/1.0/file",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'the student\'s editor file'}))),
    "mode": Activity(
        id="https://www.lip6.fr/mocah/invalidURI/activity-types/mode",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'the programming mode'}),
            description=LanguageMap({'en-US': 'student/expert mode in MrPython'}))),
    "execution": Activity(
            id="https://www.lip6.fr/mocah/invalidURI/activity-types/execution",
            definition=ActivityDefinition(
                name=LanguageMap({'en-US': 'a programming execution'}),
                description=LanguageMap({'en-US': 'execution of the student\'s editor'}))),
    "evaluation": Activity(
        id="https://www.lip6.fr/mocah/invalidURI/activity-types/interpretation",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'a programming evaluation'}),
            description=LanguageMap({'en-US': 'interpretation made by the MrPython interpretor'}))),
    "execution-error": Activity(
        id="https://www.lip6.fr/mocah/invalidURI/activity-types/execution-error",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'an execution error'}))),
    "execution-warning": Activity(
        id="https://www.lip6.fr/mocah/invalidURI/activity-types/execution-warning",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'an execution warning'}))),
    "evaluation-error": Activity(
        id="https://www.lip6.fr/mocah/invalidURI/activity-types/evaluation-error",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'an evaluation error'}))),
    "evaluation-warning": Activity(
        id="https://www.lip6.fr/mocah/invalidURI/activity-types/evaluation-warning",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'an evaluation warning'}))),
    "keyword": Activity(
        id="https://www.lip6.fr/mocah/invalidURI/activity-types/keyword",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'a keyword'}))),
    "instruction": Activity(
        id="https://www.lip6.fr/mocah/invalidURI/activity-types/instruction",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'a programming instruction'}))),
    "scrollbar": Activity(
        id="https://www.lip6.fr/mocah/invalidURI/activity-types/scrollbar",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'a scrollbar'}))),
    "text": Activity(
        id="https://www.lip6.fr/mocah/invalidURI/activity-types/text",
        definition=ActivityDefinition(
            name=LanguageMap({'en-US': 'some text'}))),
    }

if __name__ == "__main__":
    '''
    verb = verbs["opened"]
    object = activities["application"]

    statement = Statement(
        actor=actor,
        verb=verb,
        object=object,
    )
    # Send statement and receive HTTP response
    io.add_statement(statement)
    '''
    clear_stack()