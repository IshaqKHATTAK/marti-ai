
from io import BytesIO, StringIO
import logging
from PyPDF2 import PdfReader
from langchain_community.document_loaders import UnstructuredExcelLoader
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter, RecursiveJsonSplitter, HTMLSectionSplitter
from langchain_community.document_loaders.csv_loader import CSVLoader
from langchain_community.document_loaders import UnstructuredMarkdownLoader
from langchain_core.documents import Document
from ebooklib import epub
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
import pypandoc
import ebooklib

logger = logging.getLogger(__name__)


async def extract_text(file_name: str, file, file_type: str):
    try:
        logger.info(f"Text Extraction Started | File Name: {file_name} | File Type: {file_type}")
        print('Text extraction started')
        extracted_text = ""

        if file_name.endswith('.pdf'):
            with open(file_name, "wb") as f:
                contents = await file.read()
                f.write(contents)
            pdf_reader = PdfReader(BytesIO(contents))
            logger.info(f"Extracting Text From PDF | File Name: {file_name} | No. of Pages: {len(pdf_reader.pages)}")
            extracted_text = '\n'.join([page.extract_text() for page in pdf_reader.pages])
        
        elif file_name.endswith('.md'):
            loader = UnstructuredMarkdownLoader(file_name)
            data = loader.load()
            logger.info(f"Extracting Text From markdown | File Name: {file_name} | No. of Pages: {len(data)}")
            extracted_text = '\n'.join([page.page_content for page in data])
        
        elif file_name.endswith('.txt'):
            with open(file_name, "wb") as f:
                contents = await file.read()
                f.write(contents)
            with StringIO(contents.decode('utf-8')) as stringio:
                logger.info(f"Extracting Text From Plain text File | File Name: {file_name} | Bytes Size: {len(contents)}")
                extracted_text = stringio.read()

        elif file_name.endswith('.csv'):
            return

        else:
            logger.error(f"Unsupported or unknown file type: {file_type} for file {file_name}")
            raise ValueError(f"Unsupported file type: {file_name} with MIME type: {file_type}")

        if _is_null_empty_or_whitespace(extracted_text):
            raise Exception("No text was extracted from the file")
        
        logger.info(f"Text Extraction Ended | File Name: {file_name} | File Type: {file_type}")
        return extracted_text
    
    except Exception as ex:
        raise Exception(f"Unable to extract text | File name: {file_name} | File Type: {file_type} | Error: {ex}")
    
def _is_null_empty_or_whitespace(s: str):
    return not s or not s.strip()

def _chunk_text(text: str):
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=4000, chunk_overlap=100, separators=[" ", ",", "\n"])
    chunked_texts = text_splitter.split_text(text)
    return chunked_texts

def _chunk_markdown(text: str):
    headers_to_split_on = [
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
        ("####", "Header 4"),
        ("#####", "Header 5"),
        ("######", "Header 6"),
    ]
    markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on)
    md_header_splits = markdown_splitter.split_text(text)
    return md_header_splits

def _chunk_json(json_data):
    splitter = RecursiveJsonSplitter(max_chunk_size=300)
    json_chunks = splitter.split_json(json_data=json_data)
    return json_chunks

def _chunk_html(html_Data):
    html_splitter = HTMLSectionSplitter()
    html_header_splits = html_splitter.split_text(html_Data)
    return html_header_splits

def _chunk_csv(csv_path):
    loader = CSVLoader(file_path=csv_path)
    return loader.load()

def _chunk_xlsx(xls_path):
    loader = UnstructuredExcelLoader(xls_path, mode="elements")
    return loader.load()

def convert_epub_to_text(file_path):
    book = epub.read_epub(file_path)
    text = ''
    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_body_content(), 'html.parser')
            text += soup.get_text()
    return text

def convert_rtf_to_docx(file_path, output_path):
    pypandoc.convert_file(file_path, 'docx', outputfile=output_path)
    return output_path


#This function needs to be cnverted into csv instead of text.
def convert_xml_to_text(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    # Flatten XML and load it into a DataFrame
    rows = []
    for student in root.findall('student'):
        row = {child.tag: child.text for child in student}
        rows.append(row)

