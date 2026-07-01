"""애드팝콘 raw 데이터 파싱 → data.json ad_revenue 섹션 업데이트.

사용법:
  1. 애드팝콘 대시보드에서 raw 데이터 복사 (탭 구분 TSV)
  2. 아래 RAW 변수에 붙여넣기
  3. python dashboard/fetch_ad_revenue.py
"""
import json
from collections import defaultdict

# 환율 고정 (1 USD = KRW)
USD_KRW = 1480

# 애드팝콘 raw 데이터 (탭 구분)
# 헤더: report_date, media_key, media_name, placement_id, placement_name,
#       thirdparty_name, request, response, fill_rate, impression, impression_rate,
#       click, ctr, media_cost(USD), eCPM(USD), RPR
RAW = """20260413\t216877292\t폴리볼(iOS)\tHkfQ3zD8CMu889u\t폴리볼_TEST_RV_iOS\tADPOPCORN\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260413\t216877292\t폴리볼(iOS)\tHkfQ3zD8CMu889u\t폴리볼_TEST_RV_iOS\tAppLovin(Bidding)\t0\t0\t0\t1\t0\t0\t0\t0.002014\t2.014\t0
20260413\t216877292\t폴리볼(iOS)\tMzBs4biXiUda19E\t폴리볼_TEST_전면비디오_iOS\tADPOPCORN\t7\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260413\t403840068\t폴리볼(Android)\t6OoeT1VQMxXWjuB\t폴리볼_TEST_전면비디오_Android\tADPOPCORN\t8\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260413\t403840068\t폴리볼(Android)\t6OoeT1VQMxXWjuB\t폴리볼_TEST_전면비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t8\t0\t0\t0\t0.206772\t25.8465\t0
20260413\t403840068\t폴리볼(Android)\teUOe6gEfm5Ez94U\t폴리볼_TEST_RV_Android\tADPOPCORN\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260413\t403840068\t폴리볼(Android)\teUOe6gEfm5Ez94U\t폴리볼_TEST_RV_Android\tAppLovin(Bidding)\t0\t0\t0\t2\t0\t0\t0\t0.138362\t69.181\t0
20260414\t216877292\t폴리볼(iOS)\tGYqcxb0FCcot7OL\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_iOS\tADPOPCORN\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260414\t216877292\t폴리볼(iOS)\tGYqcxb0FCcot7OL\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t1\t0\t0\t0\t0.000008\t0.008\t0
20260414\t216877292\t폴리볼(iOS)\tMzBs4biXiUda19E\t폴리볼_TEST_전면비디오_iOS\tADPOPCORN\t7\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260414\t216877292\t폴리볼(iOS)\tMzBs4biXiUda19E\t폴리볼_TEST_전면비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t13\t0\t0\t0\t0.030846\t2.372769231\t0
20260414\t216877292\t폴리볼(iOS)\tOtStazP3Y5DvkFd\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_iOS\tADPOPCORN\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260414\t216877292\t폴리볼(iOS)\tOtStazP3Y5DvkFd\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t1\t0\t0\t0\t0.000019\t0.019\t0
20260414\t403840068\t폴리볼(Android)\t6OoeT1VQMxXWjuB\t폴리볼_TEST_전면비디오_Android\tADPOPCORN\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260414\t403840068\t폴리볼(Android)\t6OoeT1VQMxXWjuB\t폴리볼_TEST_전면비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t1\t0\t0\t0\t0.029857\t29.857\t0
20260414\t403840068\t폴리볼(Android)\tcXPqLTwEJokjAQO\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_Android\tADPOPCORN\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260414\t403840068\t폴리볼(Android)\tcXPqLTwEJokjAQO\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t1\t0\t0\t0\t0.009742\t9.742\t0
20260414\t403840068\t폴리볼(Android)\teUOe6gEfm5Ez94U\t폴리볼_TEST_RV_Android\tADPOPCORN\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260414\t403840068\t폴리볼(Android)\teUOe6gEfm5Ez94U\t폴리볼_TEST_RV_Android\tAppLovin(Bidding)\t0\t0\t0\t1\t0\t0\t0\t0.043703\t43.703\t0
20260414\t403840068\t폴리볼(Android)\tHlWhGNx2LCjO6bp\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_Android\tADPOPCORN\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260414\t403840068\t폴리볼(Android)\tHlWhGNx2LCjO6bp\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t1\t0\t0\t0\t0.01256\t12.56\t0
20260414\t403840068\t폴리볼(Android)\tIol4DhZsC7KX0DB\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_Android\tADPOPCORN\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260414\t403840068\t폴리볼(Android)\tIol4DhZsC7KX0DB\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t1\t0\t0\t0\t0.008615\t8.615\t0
20260414\t403840068\t폴리볼(Android)\tTPDiIcXeefN7PzV\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_Android\tADPOPCORN\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260414\t403840068\t폴리볼(Android)\tTPDiIcXeefN7PzV\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t1\t0\t0\t0\t0.020411\t20.411\t0
20260415\t216877292\t폴리볼(iOS)\tHkfQ3zD8CMu889u\t폴리볼_TEST_RV_iOS\tADPOPCORN\t12\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260415\t216877292\t폴리볼(iOS)\tHkfQ3zD8CMu889u\t폴리볼_TEST_RV_iOS\tAppLovin(Bidding)\t0\t0\t0\t12\t0\t0\t0\t0.012912\t1.076\t0
20260415\t216877292\t폴리볼(iOS)\tMzBs4biXiUda19E\t폴리볼_TEST_전면비디오_iOS\tADPOPCORN\t52\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260415\t216877292\t폴리볼(iOS)\tMzBs4biXiUda19E\t폴리볼_TEST_전면비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t41\t0\t0\t0\t0.035371\t0.862707317\t0
20260415\t403840068\t폴리볼(Android)\t6OoeT1VQMxXWjuB\t폴리볼_TEST_전면비디오_Android\tADPOPCORN\t17\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260415\t403840068\t폴리볼(Android)\t6OoeT1VQMxXWjuB\t폴리볼_TEST_전면비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t17\t0\t0\t0\t0.228386\t13.43447059\t0
20260415\t403840068\t폴리볼(Android)\teUOe6gEfm5Ez94U\t폴리볼_TEST_RV_Android\tADPOPCORN\t10\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260415\t403840068\t폴리볼(Android)\teUOe6gEfm5Ez94U\t폴리볼_TEST_RV_Android\tAppLovin(Bidding)\t0\t0\t0\t10\t0\t0\t0\t0.176386\t17.6386\t0
20260416\t216877292\t폴리볼(iOS)\tGYqcxb0FCcot7OL\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_iOS\tADPOPCORN\t95\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260416\t216877292\t폴리볼(iOS)\tGYqcxb0FCcot7OL\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t133\t0\t0\t0\t1.430188\t10.75329323\t0
20260416\t216877292\t폴리볼(iOS)\tHkfQ3zD8CMu889u\t폴리볼_TEST_RV_iOS\tADPOPCORN\t8\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260416\t216877292\t폴리볼(iOS)\tHkfQ3zD8CMu889u\t폴리볼_TEST_RV_iOS\tAppLovin(Bidding)\t0\t0\t0\t8\t0\t0\t0\t0.012479\t1.559875\t0
20260416\t216877292\t폴리볼(iOS)\tMzBs4biXiUda19E\t폴리볼_TEST_전면비디오_iOS\tADPOPCORN\t4\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260416\t216877292\t폴리볼(iOS)\tMzBs4biXiUda19E\t폴리볼_TEST_전면비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t4\t0\t0\t0\t0.003803\t0.95075\t0
20260416\t216877292\t폴리볼(iOS)\tOtStazP3Y5DvkFd\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_iOS\tADPOPCORN\t20\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260416\t216877292\t폴리볼(iOS)\tOtStazP3Y5DvkFd\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t42\t0\t0\t0\t0.539166\t12.83728571\t0
20260416\t216877292\t폴리볼(iOS)\tpFirhSHtXILBTOP\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_iOS\tADPOPCORN\t165\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260416\t216877292\t폴리볼(iOS)\tpFirhSHtXILBTOP\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t226\t0\t0\t0\t2.103312\t9.306690265\t0
20260416\t216877292\t폴리볼(iOS)\ts9paeV1566Z7eIN\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_iOS\tADPOPCORN\t14\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260416\t216877292\t폴리볼(iOS)\ts9paeV1566Z7eIN\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t15\t0\t0\t0\t0.080998\t5.399866667\t0
20260416\t403840068\t폴리볼(Android)\t6OoeT1VQMxXWjuB\t폴리볼_TEST_전면비디오_Android\tADPOPCORN\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260416\t403840068\t폴리볼(Android)\t6OoeT1VQMxXWjuB\t폴리볼_TEST_전면비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t1\t0\t0\t0\t0.000058\t0.058\t0
20260416\t403840068\t폴리볼(Android)\tcXPqLTwEJokjAQO\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_Android\tADPOPCORN\t10\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260416\t403840068\t폴리볼(Android)\tcXPqLTwEJokjAQO\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t19\t0\t0\t0\t0.423399\t22.28415789\t0
20260416\t403840068\t폴리볼(Android)\tHlWhGNx2LCjO6bp\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_Android\tADPOPCORN\t4\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260416\t403840068\t폴리볼(Android)\tHlWhGNx2LCjO6bp\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t5\t0\t0\t0\t0.043579\t8.7158\t0
20260416\t403840068\t폴리볼(Android)\tIol4DhZsC7KX0DB\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_Android\tADPOPCORN\t49\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260416\t403840068\t폴리볼(Android)\tIol4DhZsC7KX0DB\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t68\t0\t0\t0\t2.082942\t30.6315\t0
20260416\t403840068\t폴리볼(Android)\tTPDiIcXeefN7PzV\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_Android\tADPOPCORN\t79\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260416\t403840068\t폴리볼(Android)\tTPDiIcXeefN7PzV\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t118\t0\t0\t0\t2.57954\t21.86050847\t0
20260417\t216877292\t폴리볼(iOS)\tGYqcxb0FCcot7OL\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_iOS\tADPOPCORN\t133\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260417\t216877292\t폴리볼(iOS)\tGYqcxb0FCcot7OL\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t133\t0\t0\t0\t1.540905\t11.58575188\t0
20260417\t216877292\t폴리볼(iOS)\tOtStazP3Y5DvkFd\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_iOS\tADPOPCORN\t100\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260417\t216877292\t폴리볼(iOS)\tOtStazP3Y5DvkFd\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t99\t0\t0\t0\t0.891306\t9.003090909\t0
20260417\t216877292\t폴리볼(iOS)\tpFirhSHtXILBTOP\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_iOS\tADPOPCORN\t175\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260417\t216877292\t폴리볼(iOS)\tpFirhSHtXILBTOP\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t209\t0\t0\t0\t2.027285\t9.69992823\t0
20260417\t216877292\t폴리볼(iOS)\ts9paeV1566Z7eIN\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_iOS\tADPOPCORN\t37\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260417\t216877292\t폴리볼(iOS)\ts9paeV1566Z7eIN\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t36\t0\t0\t0\t0.254275\t7.063194444\t0
20260417\t403840068\t폴리볼(Android)\tcXPqLTwEJokjAQO\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_Android\tADPOPCORN\t39\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260417\t403840068\t폴리볼(Android)\tcXPqLTwEJokjAQO\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t43\t0\t0\t0\t0.753742\t17.52888372\t0
20260417\t403840068\t폴리볼(Android)\tHlWhGNx2LCjO6bp\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_Android\tADPOPCORN\t19\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260417\t403840068\t폴리볼(Android)\tHlWhGNx2LCjO6bp\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t35\t0\t0\t0\t0.42556\t12.15885714\t0
20260417\t403840068\t폴리볼(Android)\tIol4DhZsC7KX0DB\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_Android\tADPOPCORN\t80\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260417\t403840068\t폴리볼(Android)\tIol4DhZsC7KX0DB\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t87\t0\t0\t0\t2.743113\t31.53003448\t0
20260417\t403840068\t폴리볼(Android)\tTPDiIcXeefN7PzV\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_Android\tADPOPCORN\t126\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260417\t403840068\t폴리볼(Android)\tTPDiIcXeefN7PzV\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t147\t0\t0\t0\t2.236807\t15.21637415\t0
20260418\t216877292\t폴리볼(iOS)\tGYqcxb0FCcot7OL\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_iOS\tADPOPCORN\t147\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260418\t216877292\t폴리볼(iOS)\tGYqcxb0FCcot7OL\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t136\t0\t0\t0\t1.151119\t8.464110294\t0
20260418\t216877292\t폴리볼(iOS)\tOtStazP3Y5DvkFd\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_iOS\tADPOPCORN\t119\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260418\t216877292\t폴리볼(iOS)\tOtStazP3Y5DvkFd\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t136\t0\t0\t0\t1.087714\t7.997897059\t0
20260418\t216877292\t폴리볼(iOS)\tpFirhSHtXILBTOP\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_iOS\tADPOPCORN\t271\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260418\t216877292\t폴리볼(iOS)\tpFirhSHtXILBTOP\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t242\t0\t0\t0\t1.159986\t4.793330579\t0
20260418\t216877292\t폴리볼(iOS)\ts9paeV1566Z7eIN\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_iOS\tADPOPCORN\t32\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260418\t216877292\t폴리볼(iOS)\ts9paeV1566Z7eIN\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_iOS\tAppLovin(Bidding)\t0\t0\t0\t34\t0\t0\t0\t0.250715\t7.373970588\t0
20260418\t403840068\t폴리볼(Android)\tcXPqLTwEJokjAQO\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_Android\tADPOPCORN\t60\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260418\t403840068\t폴리볼(Android)\tcXPqLTwEJokjAQO\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t69\t0\t0\t0\t1.148282\t16.64176812\t0
20260418\t403840068\t폴리볼(Android)\tHlWhGNx2LCjO6bp\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_Android\tADPOPCORN\t35\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260418\t403840068\t폴리볼(Android)\tHlWhGNx2LCjO6bp\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t35\t0\t0\t0\t0.204589\t5.8454\t0
20260418\t403840068\t폴리볼(Android)\tIol4DhZsC7KX0DB\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_Android\tADPOPCORN\t90\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260418\t403840068\t폴리볼(Android)\tIol4DhZsC7KX0DB\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t87\t0\t0\t0\t1.423161\t16.35817241\t0
20260418\t403840068\t폴리볼(Android)\tTPDiIcXeefN7PzV\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_Android\tADPOPCORN\t181\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260418\t403840068\t폴리볼(Android)\tTPDiIcXeefN7PzV\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_Android\tAppLovin(Bidding)\t0\t0\t0\t178\t0\t0\t0\t1.80904\t10.16314607\t0
20260419\t216877292\t폴리볼(iOS)\tGYqcxb0FCcot7OL\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_iOS\tADPOPCORN\t175\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260419\t216877292\t폴리볼(iOS)\tOtStazP3Y5DvkFd\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_iOS\tADPOPCORN\t151\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260419\t216877292\t폴리볼(iOS)\tpFirhSHtXILBTOP\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_iOS\tADPOPCORN\t278\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260419\t216877292\t폴리볼(iOS)\ts9paeV1566Z7eIN\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_iOS\tADPOPCORN\t45\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260419\t403840068\t폴리볼(Android)\tcXPqLTwEJokjAQO\t폴리볼_응모_직관응모_추가응모_최초참여_전면비디오_Android\tADPOPCORN\t69\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260419\t403840068\t폴리볼(Android)\tHlWhGNx2LCjO6bp\t폴리볼_응모_직관응모_추가응모_추가참여_리워드비디오_Android\tADPOPCORN\t43\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260419\t403840068\t폴리볼(Android)\tIol4DhZsC7KX0DB\t폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_Android\tADPOPCORN\t84\t0\t0\t0\t0\t0\t0\t0\t0\t0
20260419\t403840068\t폴리볼(Android)\tTPDiIcXeefN7PzV\t폴리볼_픽_승부예측_적중보상확대_추가참여_리워드비디오_Android\tADPOPCORN\t205\t0\t0\t0\t0\t0\t0\t0\t0\t0"""


def parse_placement(name: str):
    """placement_name에서 메타 추출.
    예: 폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_iOS
    예: MAX_폴리볼_픽_승부예측_적중보상확대_최초참여_전면비디오_iOS
    예: MAX_폴리볼_커뮤니티_추가응원_전면비디오_iOS (cheer는 5단)
    """
    parts = name.split("_")
    # MAX_ 접두어 제거
    if parts and parts[0] == "MAX":
        parts = parts[1:]
    # 이제 parts[0]="폴리볼", parts[1]=카테고리, parts[-1]=OS, parts[-2]=format
    if len(parts) < 4:
        return {"category": "", "phase": "", "format": "", "os": ""}
    cat_kr = parts[1]
    category = ("pick" if cat_kr in ("픽","예측") else "apply" if cat_kr == "응모"
               else "cheer" if cat_kr == "커뮤니티"
               else "minigame" if cat_kr == "미니게임" else "")
    os_ = parts[-1]
    fmt_kr = parts[-2]
    fmt = "interstitial" if fmt_kr == "전면비디오" else "rv" if fmt_kr == "리워드비디오" else ""
    # phase: pick/apply는 최초/추가참여 존재, cheer는 없음
    phase = ""
    for p in parts:
        if p == "최초참여": phase = "initial"; break
        if p == "추가참여": phase = "repeat"; break
    return {"category": category, "phase": phase, "format": fmt, "os": os_}


# 폴리볼 실제 8개 placement (TEST 및 다른 앱 placement 제외)
VALID_PIDS = {
    "GYqcxb0FCcot7OL", "OtStazP3Y5DvkFd", "pFirhSHtXILBTOP", "s9paeV1566Z7eIN",
    "cXPqLTwEJokjAQO", "HlWhGNx2LCjO6bp", "Iol4DhZsC7KX0DB", "TPDiIcXeefN7PzV",
    # cheer/응원 placements (added 2026-05)
    "SLWgNe35q3Tg591", "tEpReI0kgY8w06c", "1Pex8bcNJamgtue",
    # 미니게임 placements (5/27 런칭)
    "FSaz326Fe6rxhwM", "Nb4rF8saJdQBWnd", "B2i6xmXrlDBDPj3", "GWzo6tAWuRYgosX",
    # 예측 퀴즈재참여
    "FlD9F3pet8AsYHv",
}

def parse_raw(raw: str):
    """TSV raw → dict {(date, placement_id): {...merged...}}

    규칙:
      - 폴리볼 8개 placement만 유지 (VALID_PIDS)
      - ADPOPCORN = request (유저 시청 시도)
      - 그 외 모든 미디에이션 네트워크(AppLovin Bidding/Waterfall, Pangle, UnityAds, AdFit, Naver, Vungle 등) = impression + cost 합산
    """
    merged = {}
    for ln in raw.strip().split("\n"):
        cols = ln.split("\t")
        if len(cols) < 16:
            continue
        date_s, _mk, _mn, pid, pname, tp, req, _resp, _fr, imp, _ir, _clk, _ctr, cost_usd, _ecpm, _rpr = cols[:16]
        if pid not in VALID_PIDS:
            continue
        if "TEST" in pname or not (pname.startswith("폴리볼") or pname.startswith("MAX_폴리볼")):
            continue
        d = f"{date_s[:4]}-{date_s[4:6]}-{date_s[6:8]}"
        key = (d, pid)
        if key not in merged:
            meta = parse_placement(pname)
            merged[key] = {
                "date": d, "placement_id": pid, "placement_name": pname,
                **meta,
                "request": 0, "impression": 0, "cost_usd": 0.0,
            }
        if tp == "ADPOPCORN":
            merged[key]["request"] += int(req or 0)
        else:
            # 모든 미디에이션 네트워크 (AppLovin/Pangle/UnityAds/AdFit/Naver/Vungle 등)
            merged[key]["impression"] += int(imp or 0)
            merged[key]["cost_usd"] += float(cost_usd or 0)
    return merged


def main():
    merged = parse_raw(RAW)
    rows = []
    for r in sorted(merged.values(), key=lambda x: (x["date"], x["placement_id"])):
        cost_krw = int(round(r["cost_usd"] * USD_KRW))
        ecpm_krw = int(round(r["cost_usd"] * USD_KRW / r["impression"] * 1000)) if r["impression"] > 0 else 0
        rows.append({
            **r,
            "cost_krw": cost_krw,
            "ecpm_krw": ecpm_krw,
        })

    d = json.load(open("dashboard/data.json", "r", encoding="utf-8"))
    old_ct = len(d.get("ad_revenue", []))

    # 기존 ad_revenue 중 이번 raw의 날짜 범위에 포함된 건 제거 후 추가 (중복 방지)
    new_dates = {r["date"] for r in rows}
    existing = [r for r in d.get("ad_revenue", []) if r["date"] not in new_dates]
    d["ad_revenue"] = sorted(existing + rows, key=lambda x: (x["date"], x["placement_id"]))
    d["ad_revenue_meta"] = {"exchange_rate_usd_krw": USD_KRW}

    with open("dashboard/data.json", "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

    print(f"ad_revenue: {old_ct} -> {len(d['ad_revenue'])} records")

    # 일별 합계 출력
    by_date = defaultdict(lambda: {"usd": 0.0, "impression": 0, "request": 0})
    for r in rows:
        by_date[r["date"]]["usd"] += r["cost_usd"]
        by_date[r["date"]]["impression"] += r["impression"]
        by_date[r["date"]]["request"] += r["request"]

    print("\n[일별 합계]")
    for dt in sorted(by_date.keys()):
        v = by_date[dt]
        krw = int(v["usd"] * USD_KRW)
        print(f"  {dt}: req {v['request']:>4} / imp {v['impression']:>4} / ${v['usd']:>6.2f} / {krw:>6,}원")

    # placement별 누적
    by_pid = defaultdict(lambda: {"usd": 0.0, "impression": 0, "name": ""})
    for r in rows:
        by_pid[r["placement_id"]]["usd"] += r["cost_usd"]
        by_pid[r["placement_id"]]["impression"] += r["impression"]
        by_pid[r["placement_id"]]["name"] = r["placement_name"]
    print("\n[placement별 누적]")
    for pid, v in sorted(by_pid.items(), key=lambda x: -x[1]["usd"]):
        krw = int(v["usd"] * USD_KRW)
        nm_short = v["name"].replace("폴리볼_", "")
        print(f"  {nm_short[:50]:<50} ${v['usd']:>6.2f} / {krw:>6,}원 / imp {v['impression']:>4}")


if __name__ == "__main__":
    main()
