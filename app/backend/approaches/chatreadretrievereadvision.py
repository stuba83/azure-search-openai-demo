from typing import Any, Awaitable, Callable, Coroutine, Optional, Union

from azure.search.documents.aio import SearchClient
from azure.storage.blob.aio import ContainerClient
from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionContentPartImageParam,
    ChatCompletionContentPartParam,
    ChatCompletionMessageParam,
)
from openai_messages_token_helper import build_messages, get_token_limit

from approaches.approach import ThoughtStep
from approaches.chatapproach import ChatApproach
from core.authentication import AuthenticationHelper
from core.imageshelper import fetch_image


class ChatReadRetrieveReadVisionApproach(ChatApproach):
    """
    A multi-step approach that first uses OpenAI to turn the user's question into a search query,
    then uses Azure AI Search to retrieve relevant documents, and then sends the conversation history,
    original user question, and search results to OpenAI to generate a response.
    """

    def __init__(
        self,
        *,
        search_client: SearchClient,
        blob_container_client: ContainerClient,
        openai_client: AsyncOpenAI,
        auth_helper: AuthenticationHelper,
        chatgpt_model: str,
        chatgpt_deployment: Optional[str],  # Not needed for non-Azure OpenAI
        gpt4v_deployment: Optional[str],  # Not needed for non-Azure OpenAI
        gpt4v_model: str,
        embedding_deployment: Optional[str],  # Not needed for non-Azure OpenAI or for retrieval_mode="text"
        embedding_model: str,
        embedding_dimensions: int,
        sourcepage_field: str,
        content_field: str,
        query_language: str,
        query_speller: str,
        vision_endpoint: str,
        vision_token_provider: Callable[[], Awaitable[str]]
    ):
        self.search_client = search_client
        self.blob_container_client = blob_container_client
        self.openai_client = openai_client
        self.auth_helper = auth_helper
        self.chatgpt_model = chatgpt_model
        self.chatgpt_deployment = chatgpt_deployment
        self.gpt4v_deployment = gpt4v_deployment
        self.gpt4v_model = gpt4v_model
        self.embedding_deployment = embedding_deployment
        self.embedding_model = embedding_model
        self.embedding_dimensions = embedding_dimensions
        self.sourcepage_field = sourcepage_field
        self.content_field = content_field
        self.query_language = query_language
        self.query_speller = query_speller
        self.vision_endpoint = vision_endpoint
        self.vision_token_provider = vision_token_provider
        self.chatgpt_token_limit = get_token_limit(gpt4v_model, default_to_minimum=self.ALLOW_NON_GPT_MODELS)

    @property
    def system_message_chat_conversation(self):
        return """
        Develop a structured, user-friendly system prompt to guide a chatbot in responding to queries related to ShoreLink's various customer service scenarios. \n\nThe focus will be on maintaining clarity, user engagement, and an escalation path for unresolved issues. The chatbot should consistently identify issues, attempt resolution, and escalate when necessary.\n\n---\n\n**Task: Create a ShoreLink chatbot system prompt for customer service.** \n\nThe chatbot should assist users by addressing their issues based on predefined scenarios, identifying the query type, providing a resolution, or escalating as needed.\n\n**Context:**  \n\nThe chatbot must handle five primary query types:  \n1. Schedule Queries  \n2. App/Tech Issues  \n3. Refund Requests  \n4. Ticket/Pass Errors  \n5. Phone Network/Data Problems  \n\nFor each scenario, the chatbot should follow a structured process:  \n- **Identify the issue.**  \n- **Attempt resolution through a predefined script.**  \n- **Escalate the query if unresolved.**  \n\n---\n\n### Steps  \n1. Detect Query Category  \n   - Analyze the user’s input to determine which category (Schedule Queries, App/Tech Issues, etc.) the question falls into.  \n\n2. Issue Identification  \n   - Confirm the user's specific problem to ensure accurate assistance.  \n\n3. Provide Resolution  \n   - Use the appropriate resolution scripts corresponding to the identified category.  \n\n4. Escalation Protocol  \n   - If the issue cannot be resolved by the chatbot, escalate it to the corresponding department (Support Team, IT Support, Management) for further assistance.\n\n5. Maintain Professionalism and Empathy  \n   - Throughout the conversation, use clear and friendly language to provide a positive customer experience.\n\n---\n\n### Output Format  \n\nThe bot response should be concise, polite, and in plain language. Follow this structure for each response:  \n\n- **Step 1 (Acknowledgment):** Greet the user and confirm the nature of their issue.  \n- **Step 2 (Resolution/Action):** Provide relevant information or steps to fix the issue.  \n- **Step 3 (Escalation, if Necessary):** Explain the next steps if escalation is required.  \n\n---\n\n### Scripts and Examples  \n\n#### **1. Schedule Queries**  \n**a. Issue Identification**  \nScript: \"Hi! I can assist you with schedule information. Which schedule are you looking for today? Are you inquiring about bus or ferry service times or a specific route?\"  \n\n**b. Resolution Process**  \nScript: \"The next available bus on that route departs at [time]. Would you like me to send you a link to the full schedule or additional details about this route?\"  \n\n**c. Escalation**  \nScript: \"I’m unable to locate that information right now. Let me escalate this to our support team, and they will provide further assistance.\"  \n\n---\n\n#### **2. App/Tech Issues**  \n**a. Issue Identification**  \nScript: \"It seems like you're having trouble with the ShoreLink app. Can you share the exact issue you're facing? Are you unable to log in, or is there a problem with the app's functionality?\"  \n\n**b. Resolution Process**  \nScript: \"Let’s try troubleshooting. Please restart the app and try logging in again. If that doesn’t help, you may want to reinstall the app. Let me know if this resolves your issue.\"  \n\n**c. Escalation**  \nScript: \"I’ll need to escalate this issue to our IT support team. They will reach out to you with a resolution within 24 hours.\"  \n\n---\n\n#### **3. Refund Requests**  \n**a. Issue Identification**  \nScript: \"I understand you’re requesting a refund. Could you tell me if it’s due to a service disruption or an error during payment?\"  \n\n**b. Resolution Process**  \nScript: \"Currently, our policy does not allow for refunds. However, I can offer you physical tickets or an alternative solution. Would you prefer that?\"  \n\n**c. Escalation**  \nScript: \"I will escalate your refund request to our management team for review. They will follow up with you shortly.\"\n\n---\n\n#### **4. Ticket/Pass Errors**  \n**a. Issue Identification**  \nScript: \"I see there’s an issue with your ticket or pass. Can you provide more details about the error? Was it related to the fare, the destination, or something else?\"  \n\n**b. Resolution Process**  \nScript: \"I’ve corrected the ticket/pass error for you. The updated version will be available in your app wallet shortly.\"  \n\n**c. Escalation**  \nScript: \"This issue requires further investigation by our management team. I’ll escalate it for review, and you’ll receive an update soon.\"  \n\n---\n\n#### **5. Phone Network/Data Errors**  \n**a. Issue Identification**  \nScript: \"It looks like you’re having trouble with your phone network. Is this related to wi-fi connectivity, or are you experiencing mobile data issues?\"  \n\n**b. Resolution Process**  \nScript: \"If you are near one of our terminals or cruise ports, try moving to one of our public wi-fi zones for better connectivity. Alternatively, you can seek help from one of our on-site representatives.\"  \n\n**c. Escalation**  \nScript: \"If the issue continues, I’ll escalate this to our management team. They will help find a solution, such as issuing a physical ticket or enabling visual verification by a CSR.\"  \n\n---\n\n### Notes  \n- Ensure the chatbot can quickly identify patterns in user queries to match them to one of the five categories.  \n- The escalation messaging should clearly explain the next steps to the user to manage expectations.  \n- Responses should remain empathetic, polite, and professional at all times.  \n\n
        {follow_up_questions_prompt}
        {injected_prompt}
        """

    async def run_until_final_call(
        self,
        messages: list[ChatCompletionMessageParam],
        overrides: dict[str, Any],
        auth_claims: dict[str, Any],
        should_stream: bool = False,
    ) -> tuple[dict[str, Any], Coroutine[Any, Any, Union[ChatCompletion, AsyncStream[ChatCompletionChunk]]]]:
        seed = overrides.get("seed", None)
        use_text_search = overrides.get("retrieval_mode") in ["text", "hybrid", None]
        use_vector_search = overrides.get("retrieval_mode") in ["vectors", "hybrid", None]
        use_semantic_ranker = True if overrides.get("semantic_ranker") else False
        use_semantic_captions = True if overrides.get("semantic_captions") else False
        top = overrides.get("top", 3)
        minimum_search_score = overrides.get("minimum_search_score", 0.0)
        minimum_reranker_score = overrides.get("minimum_reranker_score", 0.0)
        filter = self.build_filter(overrides, auth_claims)

        vector_fields = overrides.get("vector_fields", ["embedding"])
        send_text_to_gptvision = overrides.get("gpt4v_input") in ["textAndImages", "texts", None]
        send_images_to_gptvision = overrides.get("gpt4v_input") in ["textAndImages", "images", None]

        original_user_query = messages[-1]["content"]
        if not isinstance(original_user_query, str):
            raise ValueError("The most recent message content must be a string.")
        past_messages: list[ChatCompletionMessageParam] = messages[:-1]

        # STEP 1: Generate an optimized keyword search query based on the chat history and the last question
        user_query_request = "Generate search query for: " + original_user_query

        query_response_token_limit = 100
        query_model = self.chatgpt_model
        query_deployment = self.chatgpt_deployment
        query_messages = build_messages(
            model=query_model,
            system_prompt=self.query_prompt_template,
            few_shots=self.query_prompt_few_shots,
            past_messages=past_messages,
            new_user_content=user_query_request,
            max_tokens=self.chatgpt_token_limit - query_response_token_limit,
        )

        chat_completion: ChatCompletion = await self.openai_client.chat.completions.create(
            model=query_deployment if query_deployment else query_model,
            messages=query_messages,
            temperature=0.0,  # Minimize creativity for search query generation
            max_tokens=query_response_token_limit,
            n=1,
            seed=seed,
        )

        query_text = self.get_search_query(chat_completion, original_user_query)

        # STEP 2: Retrieve relevant documents from the search index with the GPT optimized query

        # If retrieval mode includes vectors, compute an embedding for the query
        vectors = []
        if use_vector_search:
            for field in vector_fields:
                vector = (
                    await self.compute_text_embedding(query_text)
                    if field == "embedding"
                    else await self.compute_image_embedding(query_text)
                )
                vectors.append(vector)

        results = await self.search(
            top,
            query_text,
            filter,
            vectors,
            use_text_search,
            use_vector_search,
            use_semantic_ranker,
            use_semantic_captions,
            minimum_search_score,
            minimum_reranker_score,
        )
        sources_content = self.get_sources_content(results, use_semantic_captions, use_image_citation=True)
        content = "\n".join(sources_content)

        # STEP 3: Generate a contextual and content specific answer using the search results and chat history

        # Allow client to replace the entire prompt, or to inject into the existing prompt using >>>
        system_message = self.get_system_prompt(
            overrides.get("prompt_template"),
            self.follow_up_questions_prompt_content if overrides.get("suggest_followup_questions") else "",
        )

        user_content: list[ChatCompletionContentPartParam] = [{"text": original_user_query, "type": "text"}]
        image_list: list[ChatCompletionContentPartImageParam] = []

        if send_text_to_gptvision:
            user_content.append({"text": "\n\nSources:\n" + content, "type": "text"})
        if send_images_to_gptvision:
            for result in results:
                url = await fetch_image(self.blob_container_client, result)
                if url:
                    image_list.append({"image_url": url, "type": "image_url"})
            user_content.extend(image_list)

        response_token_limit = 1024
        messages = build_messages(
            model=self.gpt4v_model,
            system_prompt=system_message,
            past_messages=messages[:-1],
            new_user_content=user_content,
            max_tokens=self.chatgpt_token_limit - response_token_limit,
            fallback_to_default=self.ALLOW_NON_GPT_MODELS,
        )

        data_points = {
            "text": sources_content,
            "images": [d["image_url"] for d in image_list],
        }

        extra_info = {
            "data_points": data_points,
            "thoughts": [
                ThoughtStep(
                    "Prompt to generate search query",
                    query_messages,
                    (
                        {"model": query_model, "deployment": query_deployment}
                        if query_deployment
                        else {"model": query_model}
                    ),
                ),
                ThoughtStep(
                    "Search using generated search query",
                    query_text,
                    {
                        "use_semantic_captions": use_semantic_captions,
                        "use_semantic_ranker": use_semantic_ranker,
                        "top": top,
                        "filter": filter,
                        "vector_fields": vector_fields,
                        "use_text_search": use_text_search,
                    },
                ),
                ThoughtStep(
                    "Search results",
                    [result.serialize_for_results() for result in results],
                ),
                ThoughtStep(
                    "Prompt to generate answer",
                    messages,
                    (
                        {"model": self.gpt4v_model, "deployment": self.gpt4v_deployment}
                        if self.gpt4v_deployment
                        else {"model": self.gpt4v_model}
                    ),
                ),
            ],
        }

        chat_coroutine = self.openai_client.chat.completions.create(
            model=self.gpt4v_deployment if self.gpt4v_deployment else self.gpt4v_model,
            messages=messages,
            temperature=overrides.get("temperature", 0.3),
            max_tokens=response_token_limit,
            n=1,
            stream=should_stream,
            seed=seed,
        )
        return (extra_info, chat_coroutine)
