from django.core.paginator import Paginator


def section_page(records, params, key):
    """Use independent page keys and preserve every existing query parameter."""
    page = Paginator(records, 25).get_page(params.get(key))

    def link(number):
        query = params.copy()
        query[key] = str(number)
        return "?" + query.urlencode()

    return {
        "page": page,
        "previous_url": link(page.previous_page_number()) if page.has_previous() else "",
        "next_url": link(page.next_page_number()) if page.has_next() else "",
    }
