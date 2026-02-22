import io
import os
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject, BooleanObject, DictionaryObject,
    NameObject, NumberObject, TextStringObject
)

def create_acroform_pdf(filename, fields_meta):
    """
    Creates a PDF with AcroForm fields (text or checkbox).
    fields_meta: list of (name, type) tuples
    """
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    if "/AcroForm" not in writer.root_object:
        writer.root_object.update({
            NameObject("/AcroForm"): DictionaryObject({
                NameObject("/Fields"): ArrayObject(),
                NameObject("/NeedAppearances"): BooleanObject(True)
            })
        })

    acro_fields = writer.root_object["/AcroForm"]["/Fields"]

    for i, (name, ftype) in enumerate(fields_meta):
        y_pos = 700 - (i * 50)
        
        field_dict = {
            NameObject("/T"): TextStringObject(name),
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Rect"): ArrayObject([
                NumberObject(100), NumberObject(y_pos),
                NumberObject(300), NumberObject(y_pos + 30)
            ]),
        }

        if ftype == "checkbox":
            field_dict.update({
                NameObject("/FT"): NameObject("/Btn"),
                NameObject("/V"): NameObject("/Off"),
                # Appearance state for 'On' is usually /Yes
                NameObject("/AS"): NameObject("/Off"),
            })
        else:
            field_dict.update({
                NameObject("/FT"): NameObject("/Tx"),
                NameObject("/V"): TextStringObject(""),
            })

        field = DictionaryObject(field_dict)
        field_ref = writer._add_object(field)
        acro_fields.append(field_ref)

        if "/Annots" not in page:
            page[NameObject("/Annots")] = ArrayObject()
        page["/Annots"].append(field_ref)

    with open(filename, "wb") as f:
        writer.write(f)
    print(f"Created AcroForm PDF: {filename}")

def create_flat_pdf(filename):
    """
    Creates a plain PDF with no form fields.
    """
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with open(filename, "wb") as f:
        writer.write(f)
    print(f"Created Flat PDF: {filename}")

if __name__ == "__main__":
    os.makedirs("samples", exist_ok=True)
    
    # 1. Simple Form
    create_acroform_pdf("samples/simple_form.pdf", [
        ("FirstName", "text"), 
        ("LastName", "text"), 
        ("Email", "text"),
        ("Subscribe", "checkbox")
    ])
    
    # 2. Checklist/Agreement Form
    create_acroform_pdf("samples/agreement.pdf", [
        ("Full Name", "text"), 
        ("Date", "text"), 
        ("AcceptedBy", "text"),
        ("I Agree", "checkbox")
    ])
    
    # 3. Flat PDF (to test error handling)
    create_flat_pdf("samples/flat_document.pdf")
