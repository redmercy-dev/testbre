import streamlit as st
import requests
import os
import hmac
import re

# Set page configuration and hide top-right UI elements
st.set_page_config(page_title="Brevo PDF Uploader", layout="centered")
hide_streamlit_style = """
    <style>
    /* Hide hamburger menu */
    #MainMenu {visibility: hidden;}
    /* Hide footer */
    footer {visibility: hidden;}
    /* Optionally hide the header (if any) */
    header {visibility: hidden;}
    </style>
    """
st.markdown(hide_streamlit_style, unsafe_allow_html=True)


def check_password():
    """
    Returns `True` if the user enters the correct password stored in Streamlit secrets.
    """
    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if hmac.compare_digest(str(st.session_state["password"]), str(st.secrets["APP_PASSWORD"])):
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Don't store the password.
        else:
            st.session_state["password_correct"] = False

    # Return True if the password is validated.
    if st.session_state.get("password_correct", False):
        return True

    # Show input for password.
    st.text_input("Password", type="password", on_change=password_entered, key="password")
    if "password_correct" in st.session_state:
        st.error("😕 Password incorrect")
    return False


if not check_password():
    st.stop()  # Do not continue if check_password is not True.


def is_valid_phone_number(phone: str) -> bool:
    """
    Validates the phone number format.
    
    For South African numbers:
      - Without prefix: must be exactly 11 digits and start with '27' (e.g., 27789538632)
      - With a '+' prefix: must be 12 characters and start with '+27' (e.g., +27789538632)
      - With a '00' prefix: must be 13 characters and start with '0027' (e.g., 0027789538632)
      
    For other international numbers:
      - Accepts an optional '+' or '00' prefix followed by 8 to 15 digits.
    """
    # South African phone numbers without prefix (e.g., 27789538632)
    if phone.startswith("27") and len(phone) == 11:
        return True
    # South African phone numbers with '+' or '00' prefix
    if phone.startswith("+27") and len(phone) == 12:
        return True
    if phone.startswith("0027") and len(phone) == 13:
        return True
    # General international format: optional '+' or '00' followed by 8 to 15 digits
    pattern = r'^(?:\+|00)?\d{8,15}$'
    return re.match(pattern, phone) is not None


def check_existing_contact(identifier: str, identifier_type: str, api_key: str) -> bool:
    """
    Checks if an identifier (email or phone) is already associated with an existing contact.
    Returns True if the identifier exists, False otherwise.
    
    Args:
        identifier: The email or phone number to check
        identifier_type: Either 'email_id' for email or 'phone_id' for phone
        api_key: The Brevo API key
    """
    try:
        url = f"https://api.brevo.com/v3/contacts/{identifier}?identifierType={identifier_type}"
        headers = {"accept": "application/json", "api-key": api_key}
        response = requests.get(url, headers=headers)
        
        # If we get a 200 response, the identifier exists
        return response.status_code == 200
    except requests.exceptions.RequestException:
        # In case of any error, return False to allow the process to continue
        return False


def get_contact_lists(api_key):
    """
    Retrieves ALL contact lists from Brevo using pagination,
    since the API has a maximum limit of 50 per request.
    """
    all_lists = []
    offset = 0
    limit = 50  # Brevo's documented maximum
    sort = "desc"

    while True:
        # Build the URL with pagination
        url = f"https://api.brevo.com/v3/contacts/lists?limit={limit}&offset={offset}&sort={sort}"
        headers = {"accept": "application/json", "api-key": api_key}

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            st.error(f"Failed to retrieve contact lists at offset {offset}: {e}")
            break

        data = response.json()
        lists_chunk = data.get("lists", [])

        if not lists_chunk:
            # No more lists to fetch
            break

        # Add to the master list
        all_lists.extend(lists_chunk)

        # Prepare for the next "page"
        offset += limit

    return all_lists


def create_contact(email, first_name, last_name, phone, list_id, api_key):
    """
    Creates a new contact in Brevo and assigns it to a selected list.
    Now updated to pass phone number in both 'SMS' and 'WHATSAPP'.
    """
    try:
        url = "https://api.brevo.com/v3/contacts"
        payload = {
            "email": email,
            "attributes": {
                "FIRSTNAME": first_name,
                "LASTNAME": last_name,
                "SMS": phone,
                "WHATSAPP": phone  # <-- Key change: pass phone under "WHATSAPP" as well
            },
            "listIds": [list_id]
        }
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "api-key": api_key
        }
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return True
    except requests.exceptions.HTTPError as http_err:
        try:
            error_data = http_err.response.json()
            error_message = error_data.get("message", "")
            # Check for duplicate contact error or phone number issues
            if "already exists" in error_message.lower():
                st.error("A contact with this email or phone number already exists. Please check your details and try again.")
            elif "phone" in error_message.lower() or "sms" in error_message.lower():
                st.error("Invalid phone number format. For South Africa, please ensure the number is 11 digits and starts with 27 (or use +27/0027 prefixes), or follow the appropriate international format.")
            else:
                st.error(f"Failed to add contact: {error_message}")
        except ValueError:
            st.error(f"Failed to add contact: {http_err}")
        return False
    except Exception as e:
        st.error(f"Failed to add contact: {e}")
        return False


def get_contact_id(email, api_key):
    """
    Retrieves the contact ID of a newly created contact based on email.
    """
    try:
        url = f"https://api.brevo.com/v3/contacts/{email}"
        headers = {"accept": "application/json", "api-key": api_key}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json().get("id")
    except requests.exceptions.RequestException as e:
        st.error(f"Failed to retrieve contact ID: {e}")
        return None


def upload_file(contact_id, file_path, api_key):
    """
    Uploads a PDF file to the specified contact in Brevo.
    """
    try:
        url = "https://api.brevo.com/v3/crm/files"
        files = {"file": (os.path.basename(file_path), open(file_path, "rb"), "application/pdf")}
        payload = {"contactId": contact_id}
        headers = {"accept": "application/json", "api-key": api_key}
        response = requests.post(url, data=payload, files=files, headers=headers)
        response.raise_for_status()
        data = response.json()
        return f"✅ File uploaded successfully! File ID: {data.get('id')}"
    except requests.exceptions.RequestException as e:
        st.error(f"❌ File upload failed: {e}")
        return None


def main():
    """
    Main function to run the Streamlit app for adding a contact and uploading a PDF.
    """
    st.sidebar.header("Add New Contact and Upload PDF")

    # Retrieve API key from Streamlit secrets
    api_key = st.secrets["BREVO_API_KEY"]

    # User input fields
    first_name = st.sidebar.text_input("Enter First Name")
    last_name = st.sidebar.text_input("Enter Last Name")
    email = st.sidebar.text_input("Enter Contact Email")
    phone = st.sidebar.text_input("Enter Contact Phone Number")

    # Validate email if provided
    if email:
        if check_existing_contact(email, 'email_id', api_key):
            st.sidebar.error("This email address is already associated with an existing contact. Please use a different email.")

    # Validate phone number format and existence if provided
    if phone:
        if not is_valid_phone_number(phone):
            st.sidebar.error("Invalid phone number format. For South Africa, please ensure the number is 11 digits and starts with 27 (or use +27/0027 prefixes). For other countries, please follow the appropriate format.")
        elif check_existing_contact(phone, 'phone_id', api_key):
            st.sidebar.error("This phone number is already associated with an existing contact. Please use a different number.")

    # Fetch contact lists for selection
    lists = get_contact_lists(api_key)
    if not lists:
        return

    list_options = {lst["name"]: lst["id"] for lst in lists}
    selected_list = st.sidebar.selectbox("Select List to Add Contact", list_options.keys())

    # File uploader for PDF selection (optional now)
    uploaded_file = st.sidebar.file_uploader("Upload PDF (Optional)", type=["pdf"])
    file_name = uploaded_file.name if uploaded_file else None

    if st.sidebar.button("Add Contact & Send PDF"):
        # Check mandatory fields for contact creation
        if not all([first_name, last_name, email, phone]):
            st.error("Please fill in all required fields (First Name, Last Name, Email, Phone).")
            return

        # Final validations before API call
        if not is_valid_phone_number(phone):
            st.error("The phone number you entered is not in an accepted format. Please correct it.")
            return
            
        # Check if email or phone already exist
        if check_existing_contact(email, 'email_id', api_key):
            st.error("This email address is already associated with an existing contact. Please use a different email.")
            return
            
        if check_existing_contact(phone, 'phone_id', api_key):
            st.error("This phone number is already associated with an existing contact. Please use a different number.")
            return

        list_id = list_options[selected_list]
        if create_contact(email, first_name, last_name, phone, list_id, api_key):
            contact_id = get_contact_id(email, api_key)
            if contact_id:
                # If a file is uploaded, proceed with upload
                if uploaded_file and file_name:
                    file_path = file_name  # Use the original file name as temporary path
                    with open(file_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())

                    result = upload_file(contact_id, file_path, api_key)
                    os.remove(file_path)
                    if result:
                        st.success(
                            f"✅ Contact {first_name} {last_name} added to list '{selected_list}' "
                            f"and PDF '{file_name}' uploaded successfully."
                        )
                    else:
                        st.error("Failed to upload the PDF file.")
                else:
                    st.success(
                        f"✅ Contact {first_name} {last_name} added to list '{selected_list}' "
                        "without any PDF upload."
                    )
                st.info("To add another contact (and optionally upload a file), repeat the process above.")
            else:
                st.error("Failed to retrieve contact ID after creation.")
        else:
            st.error("Failed to add contact. Please check your details and try again.")


if __name__ == "__main__":
    main()
