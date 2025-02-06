import logging
from importlib import resources
from typing import Optional

from memory_profiler import profile
from playwright.async_api import Page

from browser_use.dom.history_tree_processor.view import Coordinates
from browser_use.dom.views import (
	CoordinateSet,
	DOMBaseNode,
	DOMElementNode,
	DOMState,
	DOMTextNode,
	SelectorMap,
	ViewportInfo,
)

logger = logging.getLogger(__name__)


class DomService:
	def __init__(self, page: Page):
		self.page = page
		self.xpath_cache = {}

		self._iter = 0

		self.js_code = resources.read_text('browser_use.dom', 'buildDomTree.js')

	# region - Clickable elements
	async def get_clickable_elements(
		self,
		highlight_elements: bool = True,
		focus_element: int = -1,
		viewport_expansion: int = 0,
	) -> DOMState:
		element_tree = await self._build_dom_tree(highlight_elements, focus_element, viewport_expansion)
		selector_map = self._create_selector_map(element_tree)

		return DOMState(element_tree=element_tree, selector_map=selector_map)

	def _create_selector_map(self, element_tree: DOMElementNode) -> SelectorMap:
		selector_map = {}

		def process_node(node: DOMBaseNode):
			if isinstance(node, DOMElementNode):
				if node.highlight_index is not None:
					selector_map[node.highlight_index] = node

				for child in node.children:
					process_node(child)

		process_node(element_tree)

		return selector_map

	@profile
	async def _build_dom_tree(
		self,
		highlight_elements: bool,
		focus_element: int,
		viewport_expansion: int,
	) -> DOMElementNode:
		self._iter += 1
		logger.info(f'Iteration {self._iter} --------------------------------')

		# NOTE: We execute JS code in the browser to extract important DOM information.
		#       The returned hash map contains information about the DOM tree and the
		#       relationship between the DOM elements.
		args = {
			'doHighlightElements': highlight_elements,
			'focusHighlightIndex': focus_element,
			'viewportExpansion': viewport_expansion,
		}
		eval_page = await self.page.evaluate(self.js_code, args)

		js_node_map = eval_page['map']
		js_root_id = eval_page['rootId']

		node_map = {}

		for _id, node_data in js_node_map.items():
			id = str(_id)
			node, children_ids = self._parse_node(node_data)
			if node is None:
				print(f'Node {id} is None!')
				print(node_data)
				continue
			node_map[id] = node

			# NOTE: We know that we are building the tree bottom up!
			for _child_id in children_ids:
				child_id = str(_child_id)
				child_node = node_map[child_id]

				if child_id not in node_map:
					print(f'Child node {child_id} not found!')
					continue

				node_map[id].children.append(child_node)

		html_to_dict = node_map[str(js_root_id)]

		if html_to_dict is None or not isinstance(html_to_dict, DOMElementNode):
			raise ValueError('Failed to parse HTML to dictionary')

		return html_to_dict

	def _parse_node(
		self,
		node_data: dict,
	) -> tuple[Optional[DOMBaseNode], list[int]]:
		if not node_data:
			return None, []

		# Process text nodes immediately
		if node_data.get('type') == 'TEXT_NODE':
			text_node = DOMTextNode(
				text=node_data['text'],
				is_visible=node_data['isVisible'],
				parent=None,
			)
			return text_node, []

		# Process coordinates if they exist for element nodes
		viewport_coordinates = None
		page_coordinates = None
		viewport_info = None

		if 'viewportCoordinates' in node_data:
			viewport_coordinates = CoordinateSet(
				top_left=Coordinates(**node_data['viewportCoordinates']['topLeft']),
				top_right=Coordinates(**node_data['viewportCoordinates']['topRight']),
				bottom_left=Coordinates(**node_data['viewportCoordinates']['bottomLeft']),
				bottom_right=Coordinates(**node_data['viewportCoordinates']['bottomRight']),
				center=Coordinates(**node_data['viewportCoordinates']['center']),
				width=node_data['viewportCoordinates']['width'],
				height=node_data['viewportCoordinates']['height'],
			)
		if 'pageCoordinates' in node_data:
			page_coordinates = CoordinateSet(
				top_left=Coordinates(**node_data['pageCoordinates']['topLeft']),
				top_right=Coordinates(**node_data['pageCoordinates']['topRight']),
				bottom_left=Coordinates(**node_data['pageCoordinates']['bottomLeft']),
				bottom_right=Coordinates(**node_data['pageCoordinates']['bottomRight']),
				center=Coordinates(**node_data['pageCoordinates']['center']),
				width=node_data['pageCoordinates']['width'],
				height=node_data['pageCoordinates']['height'],
			)
		if 'viewport' in node_data:
			viewport_info = ViewportInfo(
				scroll_x=node_data['viewport']['scrollX'],
				scroll_y=node_data['viewport']['scrollY'],
				width=node_data['viewport']['width'],
				height=node_data['viewport']['height'],
			)

		element_node = DOMElementNode(
			tag_name=node_data['tagName'],
			xpath=node_data['xpath'],
			attributes=node_data.get('attributes', {}),
			children=[],
			is_visible=node_data.get('isVisible', False),
			is_interactive=node_data.get('isInteractive', False),
			is_top_element=node_data.get('isTopElement', False),
			highlight_index=node_data.get('highlightIndex'),
			shadow_root=node_data.get('shadowRoot', False),
			parent=None,
			viewport_coordinates=viewport_coordinates,
			page_coordinates=page_coordinates,
			viewport_info=viewport_info,
		)

		children_ids = node_data.get('children', [])

		return element_node, children_ids
