"""parbake/parbaked_croissant.py - a measured CSV as a par-baked Croissant, unreviewed

Fails Croissant validation on purpose; NOTES.md lists the errors to work through.
Never named .croissant.json: that suffix is for a file a person has finished.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5
"""

import datetime
import json
from pathlib import Path

import croissant_to_md
from sources import dataset_stem

GENERATED_BY = "parbake 0.1"

# Not a real version: it makes the file fail validation, and people read it.
PARBAKED_CONFORMS_TO = "http://mlcommons.org/croissant/PARBAKED-DO-NOT-SUBMIT"

# On the dataset, the file object and the record set.
BANNER = (
    "PAR-BAKED. MACHINE GENERATED. NOT REVIEWED BY A HUMAN. "
    "DO NOT CITE. DO NOT SUBMIT. DO NOT PUBLISH."
)

# Matched by the bakery and present in files already written: do not reword.
FIELD_PLACEHOLDER = "NOT REVIEWED -- a human must write this description."

# Written into the file, so the list travels with it.
OUTSTANDING = [
    "Write a description for every field.",
    "Decide and record a dataType for every field.",
    "Say what each field means, and its unit, where it has one.",
    "Record the licence and the required citation.",
    "Record the responsible-AI notes: limitations, biases, personal information.",
    "Check the release status before the data goes anywhere.",
    "Remove the PARBAKED conformsTo once all of the above is true.",
]

CROISSANT_CONTEXT = {
    "@language": "en",
    "@vocab": "https://schema.org/",
    "citeAs": "cr:citeAs",
    "column": "cr:column",
    "conformsTo": "dct:conformsTo",
    "cr": "http://mlcommons.org/croissant/",
    "data": {"@id": "cr:data", "@type": "@json"},
    "dataBiases": "rai:dataBiases",
    "dataCollectionType": "rai:dataCollectionType",
    "dataSocialImpact": "rai:dataSocialImpact",
    "dataType": {"@id": "cr:dataType", "@type": "@vocab"},
    "dct": "http://purl.org/dc/terms/",
    "equivalentProperty": "cr:equivalentProperty",
    "examples": {"@id": "cr:examples", "@type": "@json"},
    "extract": "cr:extract",
    "field": "cr:field",
    "fileObject": "cr:fileObject",
    "fileProperty": "cr:fileProperty",
    "fileSet": "cr:fileSet",
    "format": "cr:format",
    "hasSyntheticData": "rai:hasSyntheticData",
    "includes": "cr:includes",
    "isLiveDataset": "cr:isLiveDataset",
    "jsonPath": "cr:jsonPath",
    "key": "cr:key",
    "md5": "cr:md5",
    "parentField": "cr:parentField",
    "path": "cr:path",
    "rai": "http://mlcommons.org/croissant/RAI/",
    "recordSet": "cr:recordSet",
    "references": "cr:references",
    "regex": "cr:regex",
    "repeated": "cr:repeated",
    "replace": "cr:replace",
    "samplingRate": "cr:samplingRate",
    "sc": "https://schema.org/",
    "separator": "cr:separator",
    "source": "cr:source",
    "subField": "cr:subField",
    "transform": "cr:transform",
}

# Copied to the non-standard block, not the fields: a schema slot would be a guess.
MEASUREMENT_KEYS = (
    "position", "rows_seen", "empty_count", "null_like_text_count", "distinct_count",
    "distinct_count_is_at_least", "shortest_value_length", "longest_value_length",
    "most_common_values", "number_count", "not_a_number_count", "is_all_empty",
    "holds_one_value", "smallest_number", "largest_number",
)


def build_parbaked_croissant(result):
    """Return the par-baked Croissant dict: file, size, columns in order.
    No types, meanings or descriptions."""
    file_details = result["file"]
    filename = Path(file_details["path"]).name

    # cr:FileObject, not sc:: one error now, 35 fewer later (NOTES.md).
    file_object = {
        "@type": "cr:FileObject",
        "@id": filename,
        "name": filename,
        "contentUrl": filename,
        "contentSize": f"{file_details['size_in_bytes']} B",
        "encodingFormat": "text/csv",
        "description": f"{BANNER} File details are measured; nothing else here is.",
    }

    fields = []
    for column_name, column in result["columns"].items():
        fields.append({
            "@type": "cr:Field",
            "@id": f"records/{column_name}",
            "name": column_name,
            # No dataType: a type is a human judgement; its absence fails validation.
            "description": FIELD_PLACEHOLDER,
            "source": {
                "fileObject": {"@id": filename},
                "extract": {"column": column_name},
            },
        })

    return {
        "@context": CROISSANT_CONTEXT,
        "@type": "sc:Dataset",
        "conformsTo": PARBAKED_CONFORMS_TO,
        "name": dataset_stem(filename),
        "description": dataset_description(result),
        "distribution": [file_object],
        "recordSet": [{
            "@type": "cr:RecordSet",
            "@id": "records",
            "name": "records",
            "description": f"{BANNER} One record per row. "
                           f"{len(fields)} fields, none of them described or typed.",
            "field": fields,
        }],
        "_parbake": provenance(result),
        "_parbake_measurements": {
            name: {key: column[key] for key in MEASUREMENT_KEYS if key in column}
            for name, column in result["columns"].items()
        },
    }


def dataset_description(result):
    """Return the dataset description: banner, scope, outstanding work."""
    file_details = result["file"]
    return (
        f"{BANNER}\n\n"
        f"This file was generated by reading {file_details['path']} and measuring it. "
        f"Scope: {file_details['scope']}, {file_details['rows_read']:,} rows over "
        f"{file_details['column_count']} columns.\n\n"
        "Everything in this file is either a measurement or a placeholder. No field has "
        "a description, a data type, a unit or a meaning, because those are judgements "
        "and no human has made them yet. It deliberately fails Croissant validation and "
        "must not be submitted, cited or published as it stands.\n\n"
        "Outstanding before this file means anything:\n"
        + "\n".join(f"  - {item}" for item in OUTSTANDING)
    )


def provenance(result):
    """Return the _parbake block: status, source, scope and check results."""
    file_details = result["file"]
    return {
        "status": "PAR-BAKED -- MACHINE GENERATED, NOT REVIEWED BY A HUMAN",
        "warning": BANNER,
        "validates": False,
        "why_it_does_not_validate": [
            "conformsTo names a version that does not exist, on purpose.",
            "No field has a dataType, because guessing one is not this tool's job.",
            "The file has no checksum recorded.",
        ],
        "outstanding_for_a_human": list(OUTSTANDING),
        "generated_by": GENERATED_BY,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_file": file_details["path"],
        "source_size_in_bytes": file_details["size_in_bytes"],
        "scope": file_details["scope"],
        "rows_read": file_details["rows_read"],
        "rows_read_is_the_whole_file": file_details["scope"] == "full scan",
        # An empty list from a run that skipped the check is not "nothing found".
        "anonymity_check_ran": result["identifiers"]["checked"],
        "columns_of_concern": list(result["identifiers"]["columns_of_concern"]),
    }


def write_parbaked_croissant(result, output_path):
    """Write the par-baked Croissant to output_path; return the document."""
    document = build_parbaked_croissant(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return document


def render_parbaked_markdown(document, renderer=croissant_to_md):
    """Return the document as Markdown, with the renderer used for finished ones."""
    # renderer is a parameter for tests.
    return renderer.render_markdown(document)
