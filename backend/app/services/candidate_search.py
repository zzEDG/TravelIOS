"""按类别执行有界 ReAct：搜索 → 阅读真实 POI → 审核 → 必要时改词重搜一次。"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Literal

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.integrations.amap_service import AmapRateLimitError, AmapServiceError, Place, distance_m

logger = logging.getLogger(__name__)


class CandidateSearchError(ValueError):
    pass


class CandidateSearchTimeout(CandidateSearchError):
    pass


class CandidateModelProtocolError(CandidateSearchError):
    pass


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(min_length=1, max_length=80,description="高德地图搜索服务的关键词")
    types: str = Field(default='', pattern=r'^(?:\d{6}(?:\|\d{6})*)?$')


class ReviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted_ids: list[str] = Field(max_length=20)
    summary: str = Field(min_length=1, max_length=300)


class SearchAction(SearchArguments):
    name: Literal['search_places']


class ReviewAction(ReviewArguments):
    name: Literal['review_candidates']


class FinishAction(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: Literal['finish']
    summary: str = Field(min_length=1, description='当前类别的检索结果摘要')


class CandidateDecision(BaseModel):
    """模型每轮自主选择一个动作，参数由对应的 Pydantic 模型约束。"""

    model_config = ConfigDict(extra='forbid')

    thought: str = Field(min_length=1, description='简述本步判断依据和下一步目的')
    action: SearchAction | ReviewAction | FinishAction = Field(discriminator='name')


_SYSTEM = '''你是一个地点检索助手，分析当前类别的要求，自主选择搜索、审核或结束动作。

# 当前任务范围
- 输入 category 是本轮唯一检索目标，context.category_description 是当前类别的具体要求。
- 可以调整同一类别的关键词，不得切换到其他类别。
- context.notes 若有整趟行程的要求，只参考与当前类别有关的限制。
- context.search_limit 是本轮搜索需要的地点最少数量。
- 当前类别的合格候选已足够时，选择 finish，不要继续其他类别的任务。

# 结构化输出
每轮返回一个 CandidateDecision，包含 thought 和 action。thought 简述本步依据；
action 由你根据 Observation 自主选择，不使用文本格式的 Thought/Action 或函数调用字符串。

可选动作及字段：
- search_places：name="search_places"，keyword 为当前类别搜索词，types 为可选高德类别代码。
  城市、起点与半径由程序固定，不能通过动作参数更改。
- review_candidates：name="review_candidates"，accepted_ids 为当前批次合格地点的真实 ID，
  summary 简述审核依据；没有合格地点则传空列表。不要编造 ID。
- finish：name="finish"，summary 概括当前类别的检索结果。完成真实搜索并审核后再结束。

程序执行 action 后会回传 Observation。根据真实结果决定下一步，不需要完成固定的动作序列。
'''


_PROMPT = ChatPromptTemplate.from_messages([
    SystemMessage(content=_SYSTEM),
    ('human', '{request_json}'),
    MessagesPlaceholder('history'),
])


@dataclass
class CandidateSearchResult:
    places: list[Place]
    failed: bool = False
    retried: bool = False


@dataclass
class _SearchSession:
    amap: object
    city: str
    origin: tuple[float, float] | None
    radius: int
    types: str
    category: str = ''
    current: dict[str, Place] | None = None
    accepted: dict[str, Place] = field(default_factory=dict)
    queries: set = field(default_factory=set)
    searches: int = 0
    failures: int = 0
    done: bool = False
    reviews: int = 0

    async def search(self, arguments):
        args = SearchArguments.model_validate(arguments)
        keyword = args.keyword.strip()
        types = self.types or args.types
      
        self.searches += 1
        self.queries.add((keyword, types))
        self.current = {}
        logger.info('Candidate search: category=%s attempt=%d keyword=%s types=%s',
                    self.category,self.searches,keyword,types)
        try:
            if self.origin:
                places = await self.amap.search_around(self.city, *self.origin, keyword, self.radius, types)
                center = Place('origin', '', '', '', *self.origin, '', '')
                places = sorted((p for p in places if distance_m(center,p) <= self.radius),
                                key=lambda p:distance_m(center,p))
            else:
                places = await self.amap.search(self.city, keyword)
        except AmapRateLimitError:
            raise
        except (AmapServiceError, httpx.HTTPError):
            self.failures += 1
            return {'search':self.searches, 'candidates':[], 'error':'高德搜索失败，请审核空结果后重试。'}
        for place in places:
            if place.poi_id and place.name:
                self.current.setdefault(place.poi_id, place)

        return {'search':self.searches, 'keyword':keyword, 'types':types,
                'candidates':[{'id':p.poi_id,'name':p.name,'category':p.category,
                               'address':p.address,'typecode':p.typecode} for p in self.current.values()]}

    def review(self, arguments):
        """审核当前输出"""
        args = ReviewArguments.model_validate(arguments)
        ids = set(args.accepted_ids)
        if self.current is None:
            return {'error':'先搜索并观察真实候选，再审核。'}
        if not ids <= self.current.keys():
            return {'error':'只能选择当前搜索返回的 POI ID，请重新审核。'}
        rejected = self.current.keys() - ids
        for poi_id in rejected:
            self.accepted.pop(poi_id, None)
        self.accepted.update({poi_id:place for poi_id,place in self.current.items() if poi_id in ids})
        needs_retry = (bool(rejected) or not self.accepted) and self.searches < 2
        self.current = None
        self.reviews += 1
        logger.info('Candidate review: category=%s searches=%d accepted=%d rejected=%d retry=%s',
                    self.category,self.searches,len(self.accepted),len(rejected),needs_retry)
        return {'accepted_ids':list(self.accepted), 'needs_retry':needs_retry,
                'instruction':'可保留合格结果，按需要调整关键词重搜，或使用 Finish 结束。' if needs_retry else '审核完成，可自行决定 Finish 或继续搜索。'}



class ReActCandidateSearch:
    def __init__(self, llm, *, sync_invoke=False, queue_timeout=999999, execution_timeout=999999):
        self.llm = llm
        self.sync_invoke = sync_invoke
        self.queue_timeout = queue_timeout
        self.execution_timeout = execution_timeout
        self._groups = asyncio.Semaphore(2)

    async def _invoke(self, chain, inputs):
        if not self.sync_invoke:
            return await chain.ainvoke(inputs)
        # 同步 SDK 在工作线程执行，事件循环继续处理地图请求和健康检查。
        # 取消时先收回线程，再让请求关闭共享 HTTP 客户端。
        job = asyncio.create_task(asyncio.to_thread(chain.invoke, inputs))
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            while not job.done():
                try:
                    await asyncio.shield(job)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not job.cancelled():
                job.exception()  # 取回异常，原始取消原因继续向上传递。
            raise

    async def search(self, amap, *, city, keyword, origin, radius, types='', context=None):
        try:
            # 类别排队不再挤占模型完成搜索、审核和 Finish 的执行时间。
            started = asyncio.get_running_loop().time()
            stage = '排队'
            acquired = False
            try:
                async with asyncio.timeout(self.queue_timeout):
                    await self._groups.acquire()
                acquired = True
                queued = asyncio.get_running_loop().time() - started
                stage = '执行'
                logger.info('Candidate execution started: category=%s queued_seconds=%.1f mode=%s budget=%ss',
                            keyword, queued, 'sync' if self.sync_invoke else 'async', self.execution_timeout)
                async with asyncio.timeout(self.execution_timeout):
                    return await self._run(amap, city, keyword, origin, radius, types, context)
            finally:
                if acquired:
                    self._groups.release()
        except (CandidateSearchError, AmapRateLimitError):
            raise
        except TimeoutError as exc:
            elapsed = asyncio.get_running_loop().time() - started
            logger.warning('Candidate timeout: category=%s stage=%s elapsed_seconds=%.1f', keyword,stage,elapsed)
            raise CandidateSearchTimeout(
                f'“{keyword}”候选检索{stage}超时，请切换模型或减少偏好后重试；已填写内容保留'
            ) from exc
        except Exception as exc:
            if isinstance(exc, UnboundLocalError):
                # 编程错误保留堆栈和变量名，便于区分模型故障与本地代码错误。
                logger.exception('Candidate ReAct local variable error: category=%s', keyword)
            else:
                logger.warning('Candidate ReAct failed: category=%s error=%s', keyword,type(exc).__name__)
            raise CandidateSearchError('候选地点检索校验失败，请稍后重试') from exc

    async def _run(self, amap, city, keyword, origin, radius, types, context):
        session = _SearchSession(amap, city, origin, radius, types, category=keyword)
        chain = _PROMPT | self.llm.with_structured_output(CandidateDecision, method='function_calling')
        request_json = json.dumps(
            {'city':city,'category':keyword,'radius_meters':radius,'context':context or {}},
            ensure_ascii=False)
        history = []
        for round_index in range(20):
            try:
                decision = CandidateDecision.model_validate(await self._invoke(
                    chain, {'request_json':request_json,'history':history}))
            except ValidationError as exc:
               history.append(AIMessage(content=json.dumps({'error':'模型输出不符合约束,请重试','details':exc.errors()},ensure_ascii=False)))
               continue
            
            action = decision.action
            logger.info('Candidate ReAct: category=%s round=%d\nThought: %s\nAction: %s',
                        keyword,round_index+1,decision.thought,action.model_dump_json())
            # 将已校验的结构化决策写入本类别消息历史，原生工具响应由适配器负责解析。
            history.append(AIMessage(content=decision.model_dump_json()))

            if isinstance(action, SearchAction):
                observation = await session.search(action)
            elif isinstance(action, ReviewAction):
                observation = session.review(action)
            elif session.current is not None or session.reviews == 0:
                observation = {'error':'Finish 前必须搜索真实地点并审核当前批次。'}
            else:
                logger.info('Candidate finished: category=%s accepted=%d',keyword,len(session.accepted))
                return CandidateSearchResult(list(session.accepted.values()),
                    session.failures == session.searches,session.searches > 1)

            result = json.dumps(observation,ensure_ascii=False)
            logger.info('Candidate Observation: category=%s %s',keyword,result)
            history.append(HumanMessage(content='Observation: '+result))
        raise CandidateSearchError('地点搜索超时，请调整偏好后重试')
