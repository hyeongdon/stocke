"""네이버 회사 개요 fetch 테스트."""
import unittest
from unittest.mock import patch

from utils.naver_company_profile import fetch_company_profile


class NaverCompanyProfileTests(unittest.TestCase):
    def test_fetch_company_profile_parses_summary(self):
        def fake_get(url, **kwargs):
            class Resp:
                def raise_for_status(self):
                    return None

                def json(self):
                    if "finance/annual" in url:
                        return {
                            "corporationSummary": {
                                "comment1": "설립 배경",
                                "comment2": "주력 사업",
                                "comment3": "해외 사업",
                            }
                        }
                    if "integration" in url:
                        return {
                            "stockName": "한전산업",
                            "industryCode": "325",
                            "industryCompareInfo": [
                                {"stockName": "한국전력"},
                                {"stockName": "한전기술"},
                            ],
                        }
                    if "basic" in url:
                        return {
                            "stockName": "한전산업",
                            "stockExchangeType": {"nameKor": "코스피"},
                        }
                    return {}

            return Resp()

        with patch("utils.naver_company_profile.requests.get", side_effect=fake_get):
            from utils import naver_company_profile as mod
            mod._CACHE.clear()
            row = fetch_company_profile("130660")

        self.assertTrue(row["ok"])
        self.assertEqual(row["stock_code"], "130660")
        self.assertEqual(row["stock_name"], "한전산업")
        self.assertEqual(len(row["overview"]), 3)
        self.assertIn("한국전력", row["industry_peers"])
        self.assertEqual(row["market"], "코스피")


if __name__ == "__main__":
    unittest.main()
